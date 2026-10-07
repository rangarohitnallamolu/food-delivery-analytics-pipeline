# Food Delivery Analytics Pipeline

> 🚧 **Work in progress.** This project is under active development; models, docs, and structure may change.

An end-to-end analytics pipeline for food delivery data (Zomato-style), built with **Snowflake** and **dbt**.
Raw CSVs are staged in S3, loaded into Snowflake through a storage integration, and modeled with dbt into analytics-ready tables.

## Project layout

```
.
├── data/        # Raw CSV source files (not committed, see below)
├── zomato/      # dbt project
│   ├── models/
│   ├── macros/
│   ├── seeds/
│   ├── snapshots/
│   ├── tests/
│   └── dbt_project.yml
└── README.md
```

## Source datasets

| File | Description | Key columns |
|---|---|---|
| `orders.csv` | Order headers with amounts, payment, status, rating, delivery time | `order_id`, `user_id`, `r_id` |
| `order_items.csv` | Line items per order | `order_item_id`, `order_id`, `f_id` |
| `restaurant.csv` | Restaurant details: city, rating, cost, cuisine | `id` |
| `menu.csv` | Restaurant menu items and prices | `menu_id`, `r_id`, `f_id` |
| `food.csv` | Food items and veg / non-veg flag | `f_id` |
| `users.csv` | Customer demographics | `user_id` |
| `reviews.csv` | Order reviews and ratings | `review_id`, `order_id` |

The raw data (~2.3 GB) is excluded from version control. Place the CSV files in `data/` locally before loading.

## Setup

```bash
conda create -n dbt python=3.11 -y
conda activate dbt
pip install dbt-core==1.8.7 dbt-snowflake
```

Create `~/.dbt/profiles.yml` with a `zomato` profile pointing to your Snowflake account (never commit this file):

```yaml
zomato:
  target: dev
  outputs:
    dev:
      type: snowflake
      account: <account_identifier>
      user: <user>
      password: <password>
      role: <role>
      warehouse: <warehouse>
      database: <database>
      schema: <schema>
      threads: 4
```

Then verify and run:

```bash
cd zomato
dbt debug
dbt run
dbt test
```
