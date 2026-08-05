"""
Pareto Analysis + SPC (Statistical Process Control) module.

Pareto: ranks segments within a dimension by their share of TOTAL late
orders (not just an "excess" vs baseline — see rca_engine for that), to
answer "where does most of our late-delivery volume concentrate, overall?"

SPC: builds a weekly p-chart (proportion late per week) with 3-sigma control
limits computed from a stable baseline window, to distinguish common-cause
variation (points inside control limits — the process behaving normally,
even if performance is chronically below target) from special-cause signals
(points outside limits — something changed and is worth investigating).
"""
import numpy as np
import pandas as pd

from src.common import get_logger, load_config
from src.etl_load import get_connection

logger = get_logger(__name__)


def pareto_by_dimension(dimension: str, top_n: int = 15) -> pd.DataFrame:
    """All-time Pareto ranking of a dimension's contribution to total late orders."""
    conn = get_connection()
    try:
        df = pd.read_sql(
            f"""SELECT {dimension} AS segment, COUNT(*) AS n_orders,
                       SUM(is_late) AS n_late
                FROM fact_order_items GROUP BY {dimension}""",
            conn,
        )
    finally:
        conn.close()

    df["late_pct"] = 100 * df["n_late"] / df["n_orders"]
    df = df.sort_values("n_late", ascending=False).reset_index(drop=True)
    total_late = df["n_late"].sum()
    df["pct_of_total_late"] = 100 * df["n_late"] / total_late
    df["cumulative_pct"] = df["pct_of_total_late"].cumsum()
    return df.head(top_n)


def spc_weekly_late_rate(baseline_weeks: int = 8) -> dict:
    """
    Build a weekly p-chart for late-delivery rate.
    Baseline = first `baseline_weeks` complete weeks of the dataset (used to
    fit the center line and control limits); all weeks are then plotted
    against those fixed limits.
    """
    cfg = load_config()
    sigma = cfg["spc"]["sigma_limits"]
    min_periods = cfg["spc"]["min_periods"]

    conn = get_connection()
    try:
        df = pd.read_sql(
            """SELECT order_week, COUNT(*) AS n, SUM(is_late) AS n_late
               FROM fact_order_items GROUP BY order_week ORDER BY order_week""",
            conn,
        )
    finally:
        conn.close()

    df["p"] = df["n_late"] / df["n"]
    if len(df) < min_periods:
        logger.warning("Not enough periods (%d) for a reliable SPC chart (need >= %d).", len(df), min_periods)

    baseline = df.head(baseline_weeks)
    p_bar = baseline["n_late"].sum() / baseline["n"].sum()
    n_bar = baseline["n"].mean()

    se = np.sqrt(p_bar * (1 - p_bar) / n_bar)
    ucl = min(1.0, p_bar + sigma * se)
    lcl = max(0.0, p_bar - sigma * se)

    df["center_line"] = p_bar
    df["ucl"] = ucl
    df["lcl"] = lcl
    df["out_of_control"] = (df["p"] > ucl) | (df["p"] < lcl)

    n_signals = int(df["out_of_control"].sum())
    logger.info(
        "SPC p-chart built: center=%.4f, UCL=%.4f, LCL=%.4f, %d/%d weeks out-of-control.",
        p_bar, ucl, lcl, n_signals, len(df),
    )

    return {
        "series": df,
        "center_line": p_bar,
        "ucl": ucl,
        "lcl": lcl,
        "baseline_weeks": baseline_weeks,
        "n_out_of_control": n_signals,
    }


if __name__ == "__main__":
    print("=== Pareto: order_region ===")
    print(pareto_by_dimension("order_region").to_string(index=False))
    print("\n=== SPC weekly late rate ===")
    spc = spc_weekly_late_rate()
    print(f"Center line: {spc['center_line']:.4f}  UCL: {spc['ucl']:.4f}  LCL: {spc['lcl']:.4f}")
    print(f"Out-of-control weeks: {spc['n_out_of_control']} / {len(spc['series'])}")
