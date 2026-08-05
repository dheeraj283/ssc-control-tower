"""
ETL layer: loads the cleaned DataFrame into the SQLite analytical database
defined in sql/schema.sql.
"""
import sqlite3
from pathlib import Path

import pandas as pd

from src.common import get_logger, load_config, resolve_path
from src.data_validation import run as validate_and_clean

logger = get_logger(__name__)

COLUMN_MAP = {
    "Order Item Id": "order_item_id",
    "Order Id": "order_id",
    "order date (DateOrders)": "order_date",
    "shipping date (DateOrders)": "shipping_date",
    "order_period": "order_period",
    "order_week": "order_week",
    "Type": "order_type",
    "Order Status": "order_status",
    "Delivery Status": "delivery_status",
    "Shipping Mode": "shipping_mode",
    "is_late": "is_late",
    "is_on_time": "is_on_time",
    "is_cancelled": "is_cancelled",
    "Days for shipping (real)": "days_for_shipping_real",
    "Days for shipment (scheduled)": "days_for_shipment_scheduled",
    "shipping_delay_days": "shipping_delay_days",
    "Market": "market",
    "Order Region": "order_region",
    "Order Country": "order_country",
    "Order State": "order_state",
    "Order City": "order_city",
    "Order Customer Id": "customer_id",
    "Customer Segment": "customer_segment",
    "Category Name": "category_name",
    "Department Name": "department_name",
    "Product Name": "product_name",
    "Order Item Quantity": "order_item_quantity",
    "Sales": "sales",
    "Order Item Total": "order_item_total",
    "Order Profit Per Order": "order_profit_per_order",
    "Sales per customer": "sales_per_customer",
    "Order Item Discount": "order_item_discount",
}


def build_database(force: bool = True) -> Path:
    """Validate, clean, and load the dataset into SQLite. Returns db path."""
    cfg = load_config()
    db_path = resolve_path(cfg["paths"]["sqlite_db"])
    schema_path = resolve_path("sql/schema.sql")
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if db_path.exists() and not force:
        logger.info("Database already exists at %s, skipping rebuild.", db_path)
        return db_path

    logger.info("Running validation + cleaning pipeline...")
    clean_df, quality_report = validate_and_clean()

    fact_df = clean_df[list(COLUMN_MAP.keys())].rename(columns=COLUMN_MAP)
    fact_df["order_date"] = fact_df["order_date"].astype(str)
    fact_df["shipping_date"] = fact_df["shipping_date"].astype(str)

    logger.info("Building SQLite database at %s ...", db_path)
    conn = sqlite3.connect(db_path)
    try:
        with open(schema_path, "r") as f:
            conn.executescript(f.read())
        fact_df.to_sql("fact_order_items", conn, if_exists="append", index=False)
        conn.commit()

        cur = conn.execute("SELECT COUNT(*) FROM fact_order_items")
        row_count = cur.fetchone()[0]
        logger.info("Loaded %d rows into fact_order_items.", row_count)

        # persist quality report for the dashboard's Data Quality tab
        import json
        quality_path = resolve_path(cfg["paths"]["processed_dir"]) / "quality_report.json"
        with open(quality_path, "w") as f:
            json.dump(quality_report, f, indent=2, default=str)
        logger.info("Quality report written to %s", quality_path)
    finally:
        conn.close()

    return db_path


def get_connection() -> sqlite3.Connection:
    """Return a connection to the built database (build it first if needed)."""
    cfg = load_config()
    db_path = resolve_path(cfg["paths"]["sqlite_db"])
    if not db_path.exists():
        logger.info("Database not found, building it now...")
        build_database()
    return sqlite3.connect(db_path)


if __name__ == "__main__":
    build_database(force=True)
