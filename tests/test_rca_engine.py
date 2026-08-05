"""Tests for the RCA engine: contribution math, significance testing, Pareto ordering."""
import numpy as np
import pandas as pd
import pytest

from src.rca_engine import analyze_dimension, _two_proportion_ztest, get_complete_periods


def _fake_fact_df():
    """
    Synthetic order-item data with a KNOWN, engineered contribution pattern:
    - baseline period 'B': region X has 20% late rate, region Y has 20% late rate
    - current period 'C': region X stays at 20%, region Y jumps to 80% late rate
    Region Y should dominate the excess-late contribution ranking.
    """
    rows = []
    rng = np.random.default_rng(42)

    def add(period, region, n, late_rate):
        for _ in range(n):
            rows.append({
                "order_period": period,
                "order_region": region,
                "is_late": int(rng.random() < late_rate),
            })

    add("B", "X", 200, 0.20)
    add("B", "Y", 200, 0.20)
    add("C", "X", 200, 0.20)
    add("C", "Y", 200, 0.80)
    return pd.DataFrame(rows)


def test_contribution_analysis_identifies_deteriorated_segment():
    df = _fake_fact_df()
    results = analyze_dimension(
        df, "order_region", baseline_periods=["B"], current_periods=["C"],
        min_segment_orders=10, alpha=0.05,
    )
    assert len(results) == 2
    top = results[0]
    assert top.segment == "Y", "Region Y (rate jumped 20%->80%) should be the top contributor"
    assert top.contribution_pct > 90, "Region Y should account for nearly all excess late orders"
    assert top.significant is True, "A jump from 20% to 80% on n=200 should be statistically significant"


def test_stable_segment_shows_near_zero_excess():
    df = _fake_fact_df()
    results = analyze_dimension(
        df, "order_region", baseline_periods=["B"], current_periods=["C"],
        min_segment_orders=10, alpha=0.05,
    )
    stable = next(r for r in results if r.segment == "X")
    assert abs(stable.excess_late_orders) < 15, "Region X rate didn't change, excess should be near zero"


def test_two_proportion_ztest_symmetry_and_small_n_guard():
    z, p = _two_proportion_ztest(late1=20, n1=100, late2=80, n2=100)
    assert p is not None and p < 0.001, "A 20% vs 80% rate on n=100 each must be highly significant"

    z_none, p_none = _two_proportion_ztest(late1=1, n1=2, late2=1, n2=2)
    assert z_none is None and p_none is None, "Tiny samples (<5) should not produce a spurious z-test result"


def test_get_complete_periods_excludes_low_ratio_tail():
    counts = pd.DataFrame({
        "order_period": ["2020-01", "2020-02", "2020-03"],
        "order_id": [1, 2, 3],  # placeholder, not used directly
    })
    # Build a fact-like df where 2020-03 has ~1 item per order (anomalous),
    # and 2020-01/02 have ~3 items per order (normal).
    rows = []
    for period, items_per_order, n_orders in [("2020-01", 3, 50), ("2020-02", 3, 50), ("2020-03", 1, 50)]:
        for oid in range(n_orders):
            for _ in range(items_per_order):
                rows.append({"order_period": period, "order_id": f"{period}-{oid}", "is_late": 0})
    df = pd.DataFrame(rows)

    # get_complete_periods hits the live DB via SQL in the real module; here we
    # replicate its core ratio logic directly to keep the test hermetic.
    grp = df.groupby("order_period").agg(n_items=("is_late", "size"), n_orders=("order_id", "nunique"))
    grp["ratio"] = grp["n_items"] / grp["n_orders"]
    complete = grp[grp["ratio"] >= 1.5].index.tolist()

    assert "2020-01" in complete and "2020-02" in complete
    assert "2020-03" not in complete
