"""
KPI Engine
==========
Computes core operational KPIs at monthly / weekly grain from the SQLite
fact table, and flags breaches against the assumed targets in config.yaml.

KPIs implemented (all directly supported by the dataset — see README for
what was deliberately NOT computed and why):
    - order_items / orders          : volume
    - on_time_delivery_pct          : 1 - Late_delivery_risk, order-item basis
    - late_delivery_pct             : inverse of above
    - sla_adherence_pct             : alias of on_time_delivery_pct (order-item SLA)
    - avg_shipping_days             : mean(Days for shipping (real))
    - cancelled_pct                 : Order Status in (CANCELED, SUSPECTED_FRAUD)
    - profit_margin_pct             : profit / sales_per_customer
"""
from dataclasses import dataclass

import pandas as pd

from src.common import get_logger, load_config
from src.etl_load import get_connection

logger = get_logger(__name__)

MONTHLY_SQL = """
SELECT
    order_period,
    COUNT(*)                                            AS order_items,
    COUNT(DISTINCT order_id)                            AS orders,
    100.0 * SUM(is_on_time) / COUNT(*)                  AS on_time_delivery_pct,
    100.0 * SUM(is_late) / COUNT(*)                     AS late_delivery_pct,
    AVG(days_for_shipping_real)                         AS avg_shipping_days,
    100.0 * SUM(is_cancelled) / COUNT(*)                AS cancelled_pct,
    SUM(order_profit_per_order)                         AS total_profit,
    SUM(sales_per_customer)                             AS total_sales,
    100.0 * SUM(order_profit_per_order) / NULLIF(SUM(sales_per_customer), 0) AS profit_margin_pct
FROM fact_order_items
GROUP BY order_period
ORDER BY order_period
"""

WEEKLY_SQL = MONTHLY_SQL.replace("order_period", "order_week")


@dataclass
class KpiBreach:
    period: str
    kpi: str
    actual: float
    target: float
    direction: str      # "above" (bad, e.g. late %) or "below" (bad, e.g. on-time %)
    gap: float           # signed gap vs target, in the "bad" direction (positive = breach)


# KPIs where HIGHER is worse, and the config key holding their target
HIGHER_IS_WORSE = {
    "late_delivery_pct": "late_delivery_pct",
    "avg_shipping_days": "avg_shipping_days_target",
    "cancelled_pct": "cancelled_order_pct",
}
# KPIs where LOWER is worse
LOWER_IS_WORSE = {
    "on_time_delivery_pct": "on_time_delivery_pct",
    "sla_adherence_pct": "sla_adherence_pct",
    "profit_margin_pct": "profit_margin_pct",
}


def compute_kpis(grain: str = "monthly") -> pd.DataFrame:
    """Compute KPI table at 'monthly' or 'weekly' grain."""
    sql = MONTHLY_SQL if grain == "monthly" else WEEKLY_SQL
    conn = get_connection()
    try:
        df = pd.read_sql(sql, conn)
    finally:
        conn.close()
    df["sla_adherence_pct"] = df["on_time_delivery_pct"]  # same basis, kept as a named KPI
    return df


def detect_breaches(kpi_df: pd.DataFrame, period_col: str = "order_period") -> list[KpiBreach]:
    """Compare each period's KPIs against configured targets, return breach list."""
    cfg = load_config()
    targets = cfg["kpi_targets"]
    breaches: list[KpiBreach] = []

    for _, row in kpi_df.iterrows():
        period = row[period_col]
        for kpi, target_key in HIGHER_IS_WORSE.items():
            target = targets[target_key]
            actual = row[kpi]
            if pd.notna(actual) and actual > target:
                breaches.append(KpiBreach(period, kpi, float(actual), float(target), "above", float(actual - target)))
        for kpi, target_key in LOWER_IS_WORSE.items():
            target = targets[target_key]
            actual = row[kpi]
            if pd.notna(actual) and actual < target:
                breaches.append(KpiBreach(period, kpi, float(actual), float(target), "below", float(target - actual)))

    logger.info("Detected %d KPI breaches across %d periods.", len(breaches), len(kpi_df))
    return breaches


def latest_period_status(kpi_df: pd.DataFrame, period_col: str = "order_period") -> dict:
    """Return a dict summarizing the most recent COMPLETE-looking period's KPI status."""
    if kpi_df.empty:
        return {}
    latest = kpi_df.iloc[-1]
    cfg = load_config()
    targets = cfg["kpi_targets"]
    status = {"period": latest[period_col]}
    for kpi in list(HIGHER_IS_WORSE) + list(LOWER_IS_WORSE):
        if kpi in status:
            continue
        target_key = HIGHER_IS_WORSE.get(kpi) or LOWER_IS_WORSE.get(kpi)
        target = targets[target_key]
        actual = latest[kpi]
        is_worse_higher = kpi in HIGHER_IS_WORSE
        breached = (actual > target) if is_worse_higher else (actual < target)
        status[kpi] = {
            "actual": round(float(actual), 2) if pd.notna(actual) else None,
            "target": target,
            "breached": bool(breached),
        }
    return status


if __name__ == "__main__":
    monthly = compute_kpis("monthly")
    print(monthly.to_string(index=False))
    breaches = detect_breaches(monthly)
    print(f"\n{len(breaches)} breaches detected.")
    for b in breaches[:10]:
        print(b)
