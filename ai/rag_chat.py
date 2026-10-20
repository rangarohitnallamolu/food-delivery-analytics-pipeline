import os
import re
import pandas as pd
import streamlit as st
import snowflake.connector
import anthropic
from dotenv import load_dotenv

load_dotenv()

CHAT_MODEL = "claude-opus-5-5"
NEW_REVIEWS = 500
CACHE_FILE = "reviews_sample.parquet"

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment


def read_reviews_from_snowflake():
    conn = snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database=os.getenv("SNOWFLAKE_DATABASE"),
        schema=os.getenv("SNOWFLAKE_SCHEMA"),
    )

    query = f"""
        SELECT REVIEW_ID, CITY, RATING, COMMENT
        FROM ZOMATO.STAGING.STG_REVIEWS
        SAMPLE ({NEW_REVIEWS} ROWS)
    """
    df = conn.cursor().execute(query).fetch_pandas_all()
    conn.close()

    df.columns = [col.lower() for col in df.columns]
    return df


@st.cache_data()
def load_reviews():
    # Save the sample to disk so the same reviews are used every run.
    # SAMPLE is random, and a different set each time would break prompt caching.
    if os.path.exists(CACHE_FILE):
        return pd.read_parquet(CACHE_FILE)

    df = read_reviews_from_snowflake()
    df = df.dropna(subset=["comment"]).reset_index(drop=True)
    df.to_parquet(CACHE_FILE)
    return df


@st.cache_data()
def build_reviews_text(df):
    # Number each review so Claude can cite them as [R12]
    lines = [
        f"[R{i}] ({row.city}, {row.rating} stars) {row.comment}"
        for i, row in enumerate(df.itertuples(index=False), start=1)
    ]
    return "\n".join(lines)


SYSTEM_PROMPT = (
    "You answer questions about customer reviews for a food delivery app. "
    "Answer ONLY using the reviews provided below. Be concise. "
    "If the reviews don't cover the question, say so. "
    "After each claim, cite the reviews that support it by number, like [R12] or [R3, R40]. "
    "When the question asks how common something is, count across all the reviews, "
    "not just a few examples."
)


def ask_claude(question, reviews_text):
    """Streams the answer into the page and returns the final message."""
    with client.beta.messages.stream(
        model=CHAT_MODEL,
        max_tokens=16000,
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # retry on a fallback model if Opus declines
        system=[
            {"type": "text", "text": SYSTEM_PROMPT},
            {
                "type": "text",
                "text": f"Customer reviews:\n{reviews_text}",
                # The reviews are identical on every question, so cache them.
                # Follow-up questions within 5 minutes reuse the cache: faster and ~90% cheaper.
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[{"role": "user", "content": question}],
    ) as stream:
        st.write_stream(stream.text_stream)
        return stream.get_final_message()


def cited_reviews(answer_text, df):
    """Pull out the [R12]-style citations so we can show the actual reviews."""
    numbers = sorted({int(n) for n in re.findall(r"R(\d+)", answer_text)})
    rows = [n - 1 for n in numbers if 1 <= n <= len(df)]
    cited = df.iloc[rows].copy()
    cited.insert(0, "ref", [f"R{n}" for n in numbers if 1 <= n <= len(df)])
    return cited


st.title("Chat with your Zomato Reviews")
st.caption(f"Reading {NEW_REVIEWS} reviews, answering with {CHAT_MODEL}")

review_df = load_reviews()
reviews_text = build_reviews_text(review_df)

question = st.text_input(
    "Ask a question about your reviews:",
    placeholder="e.g. What are the most common complaints about delivery?",
)

if question:
    st.markdown("**Answer:**")
    try:
        final = ask_claude(question, reviews_text)
    except anthropic.APIError as e:
        st.error(f"Claude API error: {e}")
        st.stop()

    if final.stop_reason == "refusal":
        st.warning("Claude declined to answer this question.")
        st.stop()

    answer = "".join(b.text for b in final.content if b.type == "text")
    usage = final.usage
    st.caption(
        f"Tokens - input: {usage.input_tokens}, "
        f"read from cache: {usage.cache_read_input_tokens}, "
        f"output: {usage.output_tokens}"
    )

    cited = cited_reviews(answer, review_df)
    with st.expander(f"Reviews cited in this answer ({len(cited)})"):
        st.dataframe(cited[["ref", "city", "rating", "comment"]], hide_index=True)