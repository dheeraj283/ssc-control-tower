"""
Root Cause Analysis (RCA) Engine
=================================
When a KPI breaches target, this module investigates WHY using dimensions
actually present in the dataset (region, market, shipping mode, category,
customer segment, order status).

Method (documented explicitly so findings are defensible in an interview):
  1. Baseline vs current period comparison   -> overall KPI deterioration
  2. Segment-level contribution analysis      -> which segments drove the
     "excess" late orders in the current period, using each segment's own
     baseline rate (isolates a RATE effect from pure volume growth)
  3. Pareto ranking of contributions          -> 80/20 view
  4. Two-proportion z-test per segment        -> flags whether the segment's
     current-vs-baseline rate shift is statistically distinguishable from
     noise (ASSOCIATION, not proof of causation)
  5. Hierarchical drill-down                  -> repeat steps 2-4 *within*
     the top contributing segment using a second dimension

IMPORTANT: this engine reports "candidate operational driver" / "primary
contributor" language. It never claims proven causality — order-item level
observational data cannot support that without a designed experiment.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats

from src.common import get_logger, load_config
from src.etl_load import get_connection

logger = get_logger(__name__)

# Periods with an order_items:orders ratio below this are treated as an
# incomplete/anomalous tail of the dataset (see docs/methodology + README)
# and excluded from baseline/current period selection.
MIN_ITEMS_PER_ORDER_RATIO = 1.5


@dataclass
class SegmentContribution:
    dimension: str
    segment: str
    baseline_rate_pct: float
    current_rate_pct: float
    current_volume: int
    current_late: int
    excess_late_orders: float          # current_late - baseline_rate * current_volume
    contribution_pct: float            # share of total positive excess
    z_stat: float | None
    p_value: float | None
    significant: bool


@dataclass
class RcaResult:
    kpi: str
    baseline_periods: list
    current_periods: list
    baseline_rate_pct: float
    current_rate_pct: float
    deterioration_pp: float            # percentage-point change (positive = worse)
    dimension_results: dict = field(default_factory=dict)   # dim -> list[SegmentContribution]
    drilldown: dict | None = None
    narrative: list = field(default_factory=list)


def _load_fact() -> pd.DataFrame:
    conn = get_connection()
    try:
        df = pd.read_sql(
            """SELECT order_period, order_region, market, shipping_mode,
                      category_name, customer_segment, order_status,
                      is_late FROM fact_order_items""",
            conn,
        )
    finally:
        conn.close()
    return df


def get_complete_periods(df: pd.DataFrame) -> list:
    """Return chronologically sorted periods excluding the anomalous data tail."""
    conn = get_connection()
    try:
        counts = pd.read_sql(
            "SELECT order_period, COUNT(*) n_items, COUNT(DISTINCT order_id) n_orders "
            "FROM fact_order_items GROUP BY order_period ORDER BY order_period", conn
        )
    finally:
        conn.close()
    counts["ratio"] = counts["n_items"] / counts["n_orders"]
    complete = counts[counts["ratio"] >= MIN_ITEMS_PER_ORDER_RATIO]["order_period"].tolist()
    excluded = counts[counts["ratio"] < MIN_ITEMS_PER_ORDER_RATIO]["order_period"].tolist()
    if excluded:
        logger.info(
            "Excluding %d anomalous/incomplete periods from RCA period selection: %s",
            len(excluded), excluded,
        )
    return sorted(complete)


def _two_proportion_ztest(late1, n1, late2, n2):
    """Two-proportion z-test. Returns (z, p) or (None, None) if not computable."""
    if n1 < 5 or n2 < 5:
        return None, None
    p1, p2 = late1 / n1, late2 / n2
    p_pool = (late1 + late2) / (n1 + n2)
    denom = p_pool * (1 - p_pool) * (1 / n1 + 1 / n2)
    if denom <= 0:
        return None, None
    z = (p2 - p1) / np.sqrt(denom)
    p_value = 2 * (1 - stats.norm.cdf(abs(z)))
    return float(z), float(p_value)


def analyze_dimension(
    df: pd.DataFrame, dimension: str, baseline_periods: list, current_periods: list,
    min_segment_orders: int, alpha: float,
) -> list[SegmentContribution]:
    """Contribution + significance analysis for one dimension (e.g. Order Region)."""
    base = df[df["order_period"].isin(baseline_periods)]
    curr = df[df["order_period"].isin(current_periods)]

    base_agg = base.groupby(dimension)["is_late"].agg(["sum", "count"]).rename(
        columns={"sum": "base_late", "count": "base_n"})
    curr_agg = curr.groupby(dimension)["is_late"].agg(["sum", "count"]).rename(
        columns={"sum": "curr_late", "count": "curr_n"})

    merged = base_agg.join(curr_agg, how="outer").fillna(0)
    merged = merged[merged["curr_n"] >= min_segment_orders]
    if merged.empty:
        return []

    merged["baseline_rate_pct"] = 100 * merged["base_late"] / merged["base_n"].replace(0, np.nan)
    merged["current_rate_pct"] = 100 * merged["curr_late"] / merged["curr_n"]
    merged["expected_late_at_baseline_rate"] = (
        merged["baseline_rate_pct"].fillna(merged["current_rate_pct"]) / 100
    ) * merged["curr_n"]
    merged["excess_late_orders"] = merged["curr_late"] - merged["expected_late_at_baseline_rate"]

    total_positive_excess = merged.loc[merged["excess_late_orders"] > 0, "excess_late_orders"].sum()

    results = []
    for seg, row in merged.iterrows():
        z, p = _two_proportion_ztest(row["base_late"], row["base_n"], row["curr_late"], row["curr_n"])
        contribution_pct = (
            100 * row["excess_late_orders"] / total_positive_excess
            if total_positive_excess > 0 and row["excess_late_orders"] > 0 else 0.0
        )
        results.append(SegmentContribution(
            dimension=dimension,
            segment=str(seg),
            baseline_rate_pct=round(float(row["baseline_rate_pct"]), 2) if pd.notna(row["baseline_rate_pct"]) else None,
            current_rate_pct=round(float(row["current_rate_pct"]), 2),
            current_volume=int(row["curr_n"]),
            current_late=int(row["curr_late"]),
            excess_late_orders=round(float(row["excess_late_orders"]), 1),
            contribution_pct=round(float(contribution_pct), 1),
            z_stat=round(z, 3) if z is not None else None,
            p_value=round(p, 4) if p is not None else None,
            significant=bool(p is not None and p < alpha),
        ))
    results.sort(key=lambda r: r.contribution_pct, reverse=True)
    return results


def run_rca(kpi: str = "late_delivery_pct", current_periods: list | None = None) -> RcaResult:
    """
    Full RCA workflow for the late-delivery KPI (the only breach-worthy KPI
    in this dataset with meaningful, non-degenerate segment variation —
    see docs/methodology.md for why on-time/profit RCA reuses this engine
    identically by swapping the `is_late` column).
    """
    cfg = load_config()
    rca_cfg = cfg["rca"]
    df = _load_fact()
    complete_periods = get_complete_periods(df)

    if current_periods is None:
        n_curr = rca_cfg["current_months"]
        current_periods = complete_periods[-n_curr:]
    n_base = rca_cfg["baseline_months"]
    remaining = [p for p in complete_periods if p not in current_periods]
    baseline_periods = remaining[-n_base:]

    base_mask = df["order_period"].isin(baseline_periods)
    curr_mask = df["order_period"].isin(current_periods)
    baseline_rate = 100 * df.loc[base_mask, "is_late"].mean()
    current_rate = 100 * df.loc[curr_mask, "is_late"].mean()
    deterioration = current_rate - baseline_rate

    result = RcaResult(
        kpi=kpi,
        baseline_periods=baseline_periods,
        current_periods=current_periods,
        baseline_rate_pct=round(baseline_rate, 2),
        current_rate_pct=round(current_rate, 2),
        deterioration_pp=round(deterioration, 2),
    )

    narrative = [
        f"Baseline period {baseline_periods[0]}..{baseline_periods[-1]} late-delivery rate: "
        f"{result.baseline_rate_pct}%. Current period {current_periods[0]}..{current_periods[-1]}: "
        f"{result.current_rate_pct}%. "
        + (
            f"Deterioration of {result.deterioration_pp} percentage points."
            if deterioration > 0.5 else
            f"Change of {result.deterioration_pp} pp — within normal noise; no material deterioration detected."
            if abs(deterioration) <= 0.5 else
            f"Improvement of {abs(result.deterioration_pp)} percentage points."
        )
    ]

    for dim in rca_cfg["dimensions"]:
        segs = analyze_dimension(
            df, dim, baseline_periods, current_periods,
            rca_cfg["min_segment_orders"], rca_cfg["significance_alpha"],
        )
        result.dimension_results[dim] = segs
        top = [s for s in segs if s.contribution_pct > 0][:3]
        if top:
            desc = "; ".join(
                f"{s.segment} ({s.contribution_pct}% of excess late orders, "
                f"{s.current_rate_pct}% late vs {s.baseline_rate_pct}% baseline"
                + (", statistically significant shift" if s.significant else ", not statistically significant")
                + ")"
                for s in top
            )
            narrative.append(f"By {dim}: top candidate contributors — {desc}.")

    # Hierarchical drill-down into the #1 contributing dimension/segment
    if result.dimension_results:
        best_dim, best_seg = None, None
        best_contrib = -1
        for dim, segs in result.dimension_results.items():
            if segs and segs[0].contribution_pct > best_contrib:
                best_contrib = segs[0].contribution_pct
                best_dim, best_seg = dim, segs[0].segment

        if best_dim and best_contrib > 0:
            sub_df = df[df[best_dim].astype(str) == best_seg]
            other_dims = [d for d in rca_cfg["dimensions"] if d != best_dim]
            drill = {}
            for d2 in other_dims:
                sub_segs = analyze_dimension(
                    sub_df, d2, baseline_periods, current_periods,
                    max(10, rca_cfg["min_segment_orders"] // 2), rca_cfg["significance_alpha"],
                )
                if sub_segs:
                    drill[d2] = sub_segs[:3]
            result.drilldown = {"within_dimension": best_dim, "within_segment": best_seg, "results": drill}
            if drill:
                d2_name, d2_segs = next(iter(drill.items()))
                narrative.append(
                    f"Drilling into {best_dim}='{best_seg}' (top contributor): within this segment, "
                    f"{d2_name} breakdown shows '{d2_segs[0].segment}' as the largest sub-contributor "
                    f"({d2_segs[0].contribution_pct}% of within-segment excess, "
                    f"{d2_segs[0].current_rate_pct}% late)."
                )

    narrative.append(
        "NOTE: these are statistical associations derived from observational order-item data. "
        "They identify 'primary contributors' / 'candidate operational drivers' for operational "
        "investigation, not proven causal root causes."
    )
    result.narrative = narrative
    return result


if __name__ == "__main__":
    res = run_rca()
    for line in res.narrative:
        print("-", line)
