"""
Data validation & cleaning layer.

Validates the raw DataCo CSV against an expected schema, reports data
quality issues, and returns a cleaned DataFrame ready for the SQL layer.

Design principle: FAIL LOUD on structural problems (missing required
columns, empty file), WARN + DROP/FIX on row-level quality issues, and
always report what was done so results are auditable.
"""
from pathlib import Path

import pandas as pd

from src.common import get_logger, load_config, resolve_path

logger = get_logger(__name__)

# Columns the pipeline depends on. If any are missing, the pipeline cannot run.
REQUIRED_COLUMNS = [
    "Type",
    "Days for shipping (real)",
    "Days for shipment (scheduled)",
    "Delivery Status",
    "Late_delivery_risk",
    "Category Name",
    "Customer Segment",
    "Market",
    "order date (DateOrders)",
    "shipping date (DateOrders)",
    "Order Id",
    "Order Item Id",
    "Order Item Quantity",
    "Order Item Total",
    "Order Profit Per Order",
    "Sales per customer",
    "Order Region",
    "Order Status",
    "Shipping Mode",
    "Product Name",
]

# Columns known (from inspection) to be sparse/unused and safe to drop.
DROP_COLUMNS = [
    "Product Description",   # 100% null in source data
    "Customer Password",     # synthetic PII placeholder, not needed for analytics
    "Customer Email",        # synthetic PII placeholder
    "Product Image",         # URL, not used for analytics
]

VALID_ORDER_STATUSES = {
    "COMPLETE", "PENDING_PAYMENT", "PROCESSING", "PENDING", "CLOSED",
    "ON_HOLD", "SUSPECTED_FRAUD", "CANCELED", "PAYMENT_REVIEW",
}


class DataValidationError(Exception):
    """Raised when the raw dataset fails structural validation."""


def validate_schema(df: pd.DataFrame) -> list[str]:
    """Check required columns exist. Raises DataValidationError if not."""
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DataValidationError(f"Missing required columns: {missing}")
    logger.info("Schema validation passed: all %d required columns present.", len(REQUIRED_COLUMNS))
    return missing


def profile_quality(df: pd.DataFrame) -> dict:
    """Compute a lightweight data-quality report (counts, not judgments)."""
    report = {
        "row_count": len(df),
        "duplicate_order_items": int(df.duplicated(subset=["Order Item Id"]).sum()),
        "null_counts": {
            col: int(n) for col, n in df.isna().sum().items() if n > 0
        },
        "invalid_order_status": int(
            (~df["Order Status"].isin(VALID_ORDER_STATUSES)).sum()
        ),
        "negative_quantity_rows": int((df["Order Item Quantity"] <= 0).sum()),
        "shipping_before_order_days": None,  # filled after date parsing
    }
    return report


def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    Clean the raw dataframe:
      - drop known-empty/PII columns
      - parse date columns
      - drop exact duplicate order-item rows
      - remove non-positive quantity rows (data entry errors)
      - derive on-time/late boolean and shipping delay fields
    Returns (clean_df, quality_report).
    """
    quality_report = profile_quality(df)

    df = df.drop(columns=[c for c in DROP_COLUMNS if c in df.columns])

    # Parse dates (source format: M/D/YYYY H:MM)
    for col in ["order date (DateOrders)", "shipping date (DateOrders)"]:
        df[col] = pd.to_datetime(df[col], format="%m/%d/%Y %H:%M", errors="coerce")

    bad_dates = df["order date (DateOrders)"].isna().sum()
    if bad_dates:
        logger.warning("Dropping %d rows with unparseable order dates.", bad_dates)
        df = df.dropna(subset=["order date (DateOrders)"])

    before = len(df)
    df = df.drop_duplicates(subset=["Order Item Id"])
    logger.info("Dropped %d duplicate Order Item Id rows.", before - len(df))

    before = len(df)
    df = df[df["Order Item Quantity"] > 0]
    logger.info("Dropped %d rows with non-positive quantity.", before - len(df))

    # Derived fields used throughout the KPI/RCA layer
    df["is_late"] = (df["Late_delivery_risk"] == 1).astype(int)
    df["is_on_time"] = 1 - df["is_late"]
    df["shipping_delay_days"] = (
        df["Days for shipping (real)"] - df["Days for shipment (scheduled)"]
    )
    df["order_period"] = df["order date (DateOrders)"].dt.to_period("M").astype(str)
    df["order_week"] = df["order date (DateOrders)"].dt.to_period("W").astype(str)
    df["is_cancelled"] = df["Order Status"].isin(["CANCELED", "SUSPECTED_FRAUD"]).astype(int)

    quality_report["shipping_before_order_days"] = int(
        (df["shipping date (DateOrders)"] < df["order date (DateOrders)"]).sum()
    )
    quality_report["rows_after_cleaning"] = len(df)
    quality_report["rows_dropped_total"] = quality_report["row_count"] - len(df)

    logger.info(
        "Cleaning complete: %d -> %d rows (%d dropped, %.2f%% retained).",
        quality_report["row_count"], len(df), quality_report["rows_dropped_total"],
        100 * len(df) / quality_report["row_count"],
    )
    return df, quality_report


def load_raw(csv_path: Path | None = None, encoding: str | None = None) -> pd.DataFrame:
    """Load the raw CSV. Falls back to config values if args are omitted."""
    cfg = load_config()
    csv_path = csv_path or resolve_path(cfg["paths"]["raw_csv"])
    encoding = encoding or cfg.get("encoding", "latin1")

    if not Path(csv_path).exists():
        raise DataValidationError(
            f"Raw data file not found at {csv_path}. "
            "See README 'Data Acquisition' section for download instructions."
        )

    df = pd.read_csv(csv_path, encoding=encoding, low_memory=False)
    if df.empty:
        raise DataValidationError("Raw CSV loaded but contains zero rows.")
    logger.info("Loaded raw CSV: %d rows, %d columns from %s", len(df), df.shape[1], csv_path)
    return df


def run() -> tuple[pd.DataFrame, dict]:
    """Full validate + clean pipeline entry point."""
    df = load_raw()
    validate_schema(df)
    clean_df, report = clean(df)
    return clean_df, report


if __name__ == "__main__":
    clean_df, report = run()
    import json
    print(json.dumps(report, indent=2, default=str))
