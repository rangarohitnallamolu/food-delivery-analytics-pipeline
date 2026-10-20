import os
import json
import snowflake.connector
import anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-opus-5-5"

SAMPLE_N = int(os.getenv("SAMPLE_N", 5))
TOPICS = ["food quality", "delivery", "pricing", "service", "packaging", "other"]
client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment

SYSTEM_PROMPT = """
You classify customer reviews for a food delivery app.

For the review you are given, return:
- sentiment_label: positive, negative, or neutral
- sentiment_score: a number between -1.0 and 1.0
- topic: the single topic the review is mostly about
- key_issue: a short phrase of 6 words or less that describes the main issue in the review, if any. If there is no issue, return null
"""

# Claude's structured outputs guarantee the reply matches this schema,
# so there is no need to describe the JSON format in the prompt.
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "sentiment_label": {"type": "string", "enum": ["positive", "negative", "neutral"]},
        "sentiment_score": {"type": "number"},
        "topic": {"type": "string", "enum": TOPICS},
        "key_issue": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["sentiment_label", "sentiment_score", "topic", "key_issue"],
    "additionalProperties": False,
}

def get_connection():
    return snowflake.connector.connect(
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database=os.getenv("SNOWFLAKE_DATABASE"),
        schema=os.getenv("SNOWFLAKE_SCHEMA"),
    )

def create_output_table(cursor):
    cursor.execute("CREATE SCHEMA IF NOT EXISTS ZOMATO.AI")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ZOMATO.AI.REVIEW_ENRICHED (
            REVIEW_ID STRING,
            SENTIMENT_LABEL STRING,
            SENTIMENT_SCORE FLOAT,
            TOPIC STRING,
            KEY_ISSUE STRING,
            MODEL STRING,
            ENRICHED_AT TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
        )
    """)

def get_reviews_to_enrich(cursor):
    cursor.execute(f"""
        SELECT REVIEW_ID, COMMENT
        FROM ZOMATO.RAW.REVIEWS
        WHERE REVIEW_ID NOT IN (SELECT REVIEW_ID FROM ZOMATO.AI.REVIEW_ENRICHED)
        LIMIT {SAMPLE_N}
    """)
    return cursor.fetchall()

def classify_review(comment):
    """Returns (labels_dict, model_that_answered)."""
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=2000,
        output_config={
            "effort": "low",  # simple classification, no deep reasoning needed
            "format": {"type": "json_schema", "schema": REVIEW_SCHEMA},
        },
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # retry on a fallback model if Opus declines
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": comment}],
    )

    if response.stop_reason == "refusal":
        raise ValueError(f"Claude declined to classify (category: {response.stop_details.category})")
    if response.stop_reason == "max_tokens":
        raise ValueError("Response was cut off before finishing")

    answer = next(b.text for b in response.content if b.type == "text")
    labels = json.loads(answer)
    labels["sentiment_score"] = max(-1.0, min(1.0, float(labels["sentiment_score"])))
    return labels, response.model

def save_results(cursor, results):
    """Insert all the enriched rows into Snowflake in one go."""
    print(f"Saving {len(results)} enriched reviews to Snowflake...")
    cursor.executemany(
        """
        INSERT INTO ZOMATO.AI.REVIEW_ENRICHED
            (review_id, sentiment_label, sentiment_score, topic, key_issue, model)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        results,
    )


def main():
    conn = get_connection()
    cursor = conn.cursor()
    create_output_table(cursor)
    reviews = get_reviews_to_enrich(cursor)

    if len(reviews) == 0:
        print("No new reviews to enrich.")
        return

    print(f"Enriching {len(reviews)} reviews...")

    results = []
    for review_id, comment in reviews:
        print(f"Classifying review {review_id}: {comment}")
        try:
            labels, model_used = classify_review(comment)
            print(f"Labels for review {review_id}: {labels}")
            results.append((
                review_id,
                labels["sentiment_label"],
                labels["sentiment_score"],
                labels["topic"],
                labels["key_issue"],
                model_used,
            ))
        except anthropic.APIError as e:
            print(f"Claude API error for review {review_id}: {e}")
            print("  cause:", repr(e.__cause__))
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            print(f"Could not classify review {review_id}: {e}")

    save_results(cursor, results)
    print(f"Saved {len(results)} enriched reviews to Snowflake.")
    conn.commit()
    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()