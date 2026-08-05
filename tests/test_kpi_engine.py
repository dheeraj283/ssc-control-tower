"""Tests for the KPI engine: correctness of KPI math and breach detection."""
import pandas as pd
import pytest

from src.kpi_engine import detect_breaches, latest_period_status


def _fake_kpi_df():
    """A small synthetic KPI table with known values (not the real dataset —
    used purely to unit-test the KPI/breach math in isolation)."""
    return pd.DataFrame([
        {  # breaches: late high, on-time low, shipping days high, cancelled high
            "order_period": "2099-01", "order_items": 100, "orders": 40,
            "on_time_delivery_pct": 50.0, "late_delivery_pct": 50.0,
            "avg_shipping_days": 5.0, "cancelled_pct": 6.0,
            "total_profit": 1000.0, "total_sales": 10000.0, "profit_margin_pct": 10.0,
        },
        {  # all within target
            "order_period": "2099-02", "order_items": 100, "orders": 40,
            "on_time_delivery_pct": 90.0, "late_delivery_pct": 10.0,
            "avg_shipping_days": 3.0, "cancelled_pct": 1.0,
            "total_profit": 2000.0, "total_sales": 10000.0, "profit_margin_pct": 20.0,
        },
    ])


def test_detect_breaches_flags_bad_period():
    df = _fake_kpi_df()
    df["sla_adherence_pct"] = df["on_time_delivery_pct"]
    breaches = detect_breaches(df)
    bad_period_breaches = [b for b in breaches if b.period == "2099-01"]
    good_period_breaches = [b for b in breaches if b.period == "2099-02"]

    assert len(bad_period_breaches) > 0, "Expected breaches in the deliberately bad period"
    assert len(good_period_breaches) == 0, "Expected no breaches in the deliberately good period"

    kpis_flagged = {b.kpi for b in bad_period_breaches}
    assert "late_delivery_pct" in kpis_flagged
    assert "on_time_delivery_pct" in kpis_flagged
    assert "cancelled_pct" in kpis_flagged
    assert "avg_shipping_days" in kpis_flagged


def test_breach_gap_is_positive_and_correct_direction():
    df = _fake_kpi_df()
    df["sla_adherence_pct"] = df["on_time_delivery_pct"]
    breaches = detect_breaches(df)
    late_breach = next(b for b in breaches if b.period == "2099-01" and b.kpi == "late_delivery_pct")
    assert late_breach.direction == "above"
    assert late_breach.gap == pytest.approx(50.0 - 15.0)

    ontime_breach = next(b for b in breaches if b.period == "2099-01" and b.kpi == "on_time_delivery_pct")
    assert ontime_breach.direction == "below"
    assert ontime_breach.gap == pytest.approx(85.0 - 50.0)


def test_latest_period_status_uses_last_row():
    df = _fake_kpi_df()
    df["sla_adherence_pct"] = df["on_time_delivery_pct"]
    status = latest_period_status(df)
    assert status["period"] == "2099-02"
    assert status["late_delivery_pct"]["breached"] is False
    assert status["on_time_delivery_pct"]["breached"] is False
