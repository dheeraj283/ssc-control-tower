"""
Supply Chain Operations Control Tower — Streamlit Dashboard
==============================================================
Run with: streamlit run dashboard/app.py

Seven tabs: Executive Overview, KPI Monitor, SLA/RCA Investigation,
Pareto Analysis, Trends & SPC, Operational Recommendations,
Data Quality / Methodology.
"""
import json
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common import load_config, resolve_path
from src.etl_load import build_database, get_connection
from src.kpi_engine import compute_kpis, detect_breaches, latest_period_status
from src.rca_engine import run_rca, get_complete_periods, _load_fact
from src.pareto_spc import pareto_by_dimension, spc_weekly_late_rate
from src.recommendations import generate_recommendations

st.set_page_config(
    page_title="Supply Chain Control Tower",
    page_icon="📦",
    layout="wide",
)

CFG = load_config()


# ------------------------------------------------------------------ caching
@st.cache_resource(show_spinner="Building analytical database (first run only)...")
def _ensure_db():
    db_path = resolve_path(CFG["paths"]["sqlite_db"])
    if not db_path.exists():
        build_database(force=True)
    return True


@st.cache_data(show_spinner=False)
def _monthly_kpis():
    return compute_kpis("monthly")


@st.cache_data(show_spinner=False)
def _weekly_kpis():
    return compute_kpis("weekly")


@st.cache_data(show_spinner=False)
def _complete_periods():
    return get_complete_periods(_load_fact())


@st.cache_data(show_spinner=False)
def _rca(current_periods_tuple):
    current_periods = list(current_periods_tuple) if current_periods_tuple else None
    return run_rca(current_periods=current_periods)


@st.cache_data(show_spinner=False)
def _pareto(dimension, top_n=15):
    return pareto_by_dimension(dimension, top_n=top_n)


@st.cache_data(show_spinner=False)
def _spc(baseline_weeks=8):
    return spc_weekly_late_rate(baseline_weeks=baseline_weeks)


@st.cache_data(show_spinner=False)
def _quality_report():
    qp = resolve_path(CFG["paths"]["processed_dir"]) / "quality_report.json"
    if qp.exists():
        with open(qp) as f:
            return json.load(f)
    return {}


_ensure_db()
monthly = _monthly_kpis()
complete_periods = _complete_periods()
monthly_complete = monthly[monthly["order_period"].isin(complete_periods)].reset_index(drop=True)
breaches_all = detect_breaches(monthly_complete)
status = latest_period_status(monthly_complete)

KPI_LABELS = {
    "on_time_delivery_pct": "On-Time Delivery %",
    "late_delivery_pct": "Late Delivery %",
    "sla_adherence_pct": "SLA Adherence %",
    "avg_shipping_days": "Avg Shipping Days",
    "cancelled_pct": "Cancelled / Fraud %",
    "profit_margin_pct": "Profit Margin %",
}

# ------------------------------------------------------------------ header
st.title("📦 Supply Chain Operations Control Tower")
st.caption(
    "KPI Monitoring · Automated Root-Cause Analysis · Executive Decision Support "
    "— built on the real DataCo Smart Supply Chain dataset (180,519 order-items, 2015–2018)."
)

tabs = st.tabs([
    "🏠 Executive Overview", "📊 KPI Monitor", "🔍 SLA / RCA Investigation",
    "📈 Pareto Analysis", "📉 Trends & SPC", "🛠️ Operational Recommendations",
    "🧪 Data Quality / Methodology",
])

# =====================================================================
# TAB 1 — EXECUTIVE OVERVIEW
# =====================================================================
with tabs[0]:
    st.subheader(f"Executive Overview — {status.get('period', 'N/A')}")
    st.caption("Latest complete reporting period. KPI targets are documented assumptions — see Methodology tab.")

    cols = st.columns(6)
    for i, (k, label) in enumerate(KPI_LABELS.items()):
        v = status.get(k, {})
        if not v:
            continue
        delta_color = "inverse" if k in ("late_delivery_pct", "avg_shipping_days", "cancelled_pct") else "normal"
        cols[i].metric(
            label,
            f"{v['actual']}{'%' if 'pct' in k else ' d'}",
            delta=f"{'target ' + str(v['target'])}",
            delta_color="off",
        )
        cols[i].markdown("🔴 Breach" if v["breached"] else "🟢 On target")

    st.divider()
    c1, c2 = st.columns([2, 1])
    with c1:
        st.markdown("##### Late-Delivery Rate Trend (Monthly, complete periods only)")
        fig = px.line(monthly_complete, x="order_period", y="late_delivery_pct", markers=True)
        fig.add_hline(y=CFG["kpi_targets"]["late_delivery_pct"], line_dash="dash", line_color="red",
                      annotation_text="Target 15%")
        fig.update_layout(height=350, yaxis_title="Late Delivery %", xaxis_title="")
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        st.markdown("##### Breach Summary")
        st.metric("KPIs breached this period", f"{sum(1 for v in status.values() if isinstance(v, dict) and v.get('breached'))} / {len(KPI_LABELS)}")
        st.metric("Total breach-periods (all-time)", len(breaches_all))
        st.info(
            "Late-delivery has been **chronically** above target every month since 2015 "
            "(44–57% vs a 15% target). This is a structural gap, not a recent event — "
            "see Trends & SPC tab.",
            icon="⚠️",
        )

    st.markdown("##### Top RCA Finding (latest complete period)")
    rca_default = _rca(tuple())
    st.write(rca_default.narrative[0])
    if rca_default.dimension_results:
        best_dim, best_segs = max(rca_default.dimension_results.items(),
                                   key=lambda kv: (kv[1][0].contribution_pct if kv[1] else -1))
        if best_segs:
            top = best_segs[0]
            st.success(
                f"**Primary candidate contributor:** `{best_dim} = {top.segment}` — "
                f"{top.contribution_pct}% of excess late orders, "
                f"{top.current_rate_pct}% late vs {top.baseline_rate_pct}% baseline "
                f"({'statistically significant' if top.significant else 'not statistically significant'}).",
                icon="🎯",
            )

# =====================================================================
# TAB 2 — KPI MONITOR
# =====================================================================
with tabs[1]:
    st.subheader("KPI Monitor")
    grain = st.radio("Grain", ["Monthly", "Weekly"], horizontal=True)
    kpi_df = monthly if grain == "Monthly" else _weekly_kpis()
    period_col = "order_period" if grain == "Monthly" else "order_week"

    kpi_choice = st.selectbox("KPI", list(KPI_LABELS.keys()), format_func=lambda k: KPI_LABELS[k])
    target_key = {
        "on_time_delivery_pct": "on_time_delivery_pct", "late_delivery_pct": "late_delivery_pct",
        "sla_adherence_pct": "sla_adherence_pct", "avg_shipping_days": "avg_shipping_days_target",
        "cancelled_pct": "cancelled_order_pct", "profit_margin_pct": "profit_margin_pct",
    }[kpi_choice]
    target_val = CFG["kpi_targets"][target_key]

    fig = px.line(kpi_df, x=period_col, y=kpi_choice, markers=True, title=KPI_LABELS[kpi_choice])
    fig.add_hline(y=target_val, line_dash="dash", line_color="red", annotation_text=f"Target {target_val}")
    fig.update_layout(height=400)
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("##### Full KPI Table")
    show_cols = [period_col, "order_items", "orders"] + list(KPI_LABELS.keys())
    st.dataframe(kpi_df[show_cols].round(2), use_container_width=True, hide_index=True)

    st.markdown("##### Breach Log")
    breach_rows = [{"Period": b.period, "KPI": KPI_LABELS.get(b.kpi, b.kpi), "Actual": round(b.actual, 2),
                     "Target": b.target, "Gap": round(b.gap, 2)} for b in breaches_all]
    st.dataframe(pd.DataFrame(breach_rows), use_container_width=True, hide_index=True)

# =====================================================================
# TAB 3 — SLA / RCA INVESTIGATION
# =====================================================================
with tabs[2]:
    st.subheader("SLA / Root Cause Analysis Investigation")
    st.caption(
        "Compares a 'current' period against a trailing 'baseline' window and decomposes "
        "the change in late-delivery rate into segment-level contributions."
    )

    default_current = complete_periods[-CFG["rca"]["current_months"]:]
    current_sel = st.multiselect("Current period(s)", complete_periods, default=default_current)
    rca_result = _rca(tuple(current_sel) if current_sel else tuple())

    c1, c2, c3 = st.columns(3)
    c1.metric("Baseline late %", f"{rca_result.baseline_rate_pct}%",
               help=f"Periods: {', '.join(rca_result.baseline_periods)}")
    c2.metric("Current late %", f"{rca_result.current_rate_pct}%",
               help=f"Periods: {', '.join(rca_result.current_periods)}")
    c3.metric("Change", f"{rca_result.deterioration_pp:+.2f} pp",
               delta=f"{rca_result.deterioration_pp:+.2f} pp", delta_color="inverse")

    st.markdown("##### Narrative Summary")
    for line in rca_result.narrative:
        st.markdown(f"- {line}")

    st.markdown("##### Dimension Drill-down")
    dim_sel = st.selectbox("Dimension", CFG["rca"]["dimensions"])
    segs = rca_result.dimension_results.get(dim_sel, [])
    if segs:
        seg_df = pd.DataFrame([{
            "Segment": s.segment, "Baseline Late %": s.baseline_rate_pct,
            "Current Late %": s.current_rate_pct, "Current Volume": s.current_volume,
            "Excess Late Orders": s.excess_late_orders, "Contribution %": s.contribution_pct,
            "z-stat": s.z_stat, "p-value": s.p_value, "Significant?": s.significant,
        } for s in segs])
        st.dataframe(seg_df, use_container_width=True, hide_index=True)
        fig = px.bar(seg_df[seg_df["Contribution %"] > 0], x="Segment", y="Contribution %",
                     color="Significant?", title=f"Contribution to excess late orders by {dim_sel}")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No segments met the minimum sample-size threshold for this dimension/period.")

    if rca_result.drilldown:
        st.markdown(
            f"##### Hierarchical Drill-down — within `{rca_result.drilldown['within_dimension']}` "
            f"= '{rca_result.drilldown['within_segment']}'"
        )
        for d2, segs2 in rca_result.drilldown["results"].items():
            st.markdown(f"**{d2}**")
            st.dataframe(pd.DataFrame([{
                "Segment": s.segment, "Current Late %": s.current_rate_pct,
                "Contribution %": s.contribution_pct, "Significant?": s.significant,
            } for s in segs2]), use_container_width=True, hide_index=True)

# =====================================================================
# TAB 4 — PARETO ANALYSIS
# =====================================================================
with tabs[3]:
    st.subheader("Pareto Analysis — Where Do Late Orders Concentrate? (All-Time)")
    dim_sel2 = st.selectbox("Dimension", CFG["rca"]["dimensions"], key="pareto_dim")
    pareto_df = _pareto(dim_sel2, top_n=20)

    fig = go.Figure()
    fig.add_bar(x=pareto_df["segment"], y=pareto_df["n_late"], name="Late Orders")
    fig.add_trace(go.Scatter(x=pareto_df["segment"], y=pareto_df["cumulative_pct"],
                              name="Cumulative %", yaxis="y2", mode="lines+markers", line_color="red"))
    fig.add_hline(y=CFG["pareto"]["cutoff_pct"], line_dash="dash", line_color="orange", yref="y2")
    fig.update_layout(
        yaxis=dict(title="Late Orders"),
        yaxis2=dict(title="Cumulative %", overlaying="y", side="right", range=[0, 100]),
        height=450, title=f"Pareto Chart — Late Orders by {dim_sel2}",
    )
    st.plotly_chart(fig, use_container_width=True)

    n_for_80 = (pareto_df["cumulative_pct"] <= 80).sum() + 1
    st.info(
        f"It takes **{n_for_80} of {len(pareto_df)}** {dim_sel2} segments to reach 80% of total late orders. "
        + ("This is a *concentrated* pattern — a small number of segments drive most failures."
           if n_for_80 <= max(3, len(pareto_df) * 0.2) else
           "This is a *broad/diffuse* pattern — failures are spread across most segments, "
           "consistent with a systemic process issue rather than a localized one."),
        icon="📌",
    )
    st.dataframe(pareto_df.round(2), use_container_width=True, hide_index=True)

# =====================================================================
# TAB 5 — TRENDS & SPC
# =====================================================================
with tabs[4]:
    st.subheader("Trends & Statistical Process Control")
    baseline_weeks = st.slider("SPC baseline window (weeks)", 4, 20, CFG["spc"]["min_periods"])
    spc = _spc(baseline_weeks=baseline_weeks)
    series = spc["series"]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=series["order_week"], y=series["p"] * 100, mode="lines+markers",
                              name="Weekly Late %",
                              marker_color=["red" if x else "steelblue" for x in series["out_of_control"]]))
    fig.add_hline(y=spc["center_line"] * 100, line_color="green", annotation_text="Center Line (CL)")
    fig.add_hline(y=spc["ucl"] * 100, line_dash="dash", line_color="red", annotation_text="UCL (+3σ)")
    fig.add_hline(y=spc["lcl"] * 100, line_dash="dash", line_color="red", annotation_text="LCL (-3σ)")
    fig.update_layout(height=450, title="Weekly Late-Delivery Rate — p-Chart", yaxis_title="Late %", xaxis_title="")
    st.plotly_chart(fig, use_container_width=True)

    st.markdown(
        f"**Center line:** {spc['center_line']*100:.2f}% · **UCL:** {spc['ucl']*100:.2f}% · "
        f"**LCL:** {spc['lcl']*100:.2f}% · **Out-of-control weeks:** {spc['n_out_of_control']} / {len(series)}"
    )
    st.caption(
        "Points inside the control band represent *common-cause* variation — the process behaving "
        "normally, even though performance is chronically far below the operational target. Points "
        "outside the band are *special-cause* signals warranting individual investigation."
    )

# =====================================================================
# TAB 6 — OPERATIONAL RECOMMENDATIONS
# =====================================================================
with tabs[5]:
    st.subheader("Operational Recommendations")
    st.caption("Transparent, rule-based corrective actions generated from the RCA findings above (not a black-box model).")
    recs = generate_recommendations(rca_default)
    for r in recs:
        color = {"High": "🔴", "Medium": "🟠", "Low": "🟢"}[r.priority]
        with st.expander(f"{color} [{r.priority}] {r.trigger}"):
            st.markdown(f"**Action:** {r.action}")
            st.markdown(f"**Owner:** {r.owner}")
            st.markdown(f"**Rationale:** {r.rationale}")

    st.divider()
    st.markdown("##### 5-Whys Starter Template — Top Finding")
    from src.exec_report import five_whys_template
    st.markdown(five_whys_template(rca_default))

# =====================================================================
# TAB 7 — DATA QUALITY / METHODOLOGY
# =====================================================================
with tabs[6]:
    st.subheader("Data Quality & Methodology")
    qr = _quality_report()
    if qr:
        c1, c2, c3 = st.columns(3)
        c1.metric("Raw rows", qr.get("row_count"))
        c2.metric("Rows after cleaning", qr.get("rows_after_cleaning"))
        c3.metric("Rows dropped", qr.get("rows_dropped_total"))
        st.markdown("**Null counts (raw file):**")
        st.json(qr.get("null_counts", {}))
        st.markdown(f"**Duplicate order-items removed:** {qr.get('duplicate_order_items')}")
        st.markdown(f"**Rows with shipping date before order date:** {qr.get('shipping_before_order_days')}")

    st.divider()
    st.markdown("##### Known Data Artifact — Truncated Tail")
    all_periods_df = compute_kpis("monthly")
    st.dataframe(all_periods_df[["order_period", "order_items", "orders"]].tail(6), hide_index=True)
    st.warning(
        "The last 4 months of the source file (2017-10 → 2018-01) show an order-item-to-order "
        "ratio collapse to ~1.0 (vs ~3.0 normally), a known artifact of this public dataset "
        "consistent with a truncated export. These periods are **excluded** from RCA baseline/"
        "current-period selection and trend charts to avoid false 'improvement' signals caused "
        "purely by incomplete data.",
        icon="⚠️",
    )

    st.divider()
    st.markdown("##### Methodology & Assumptions")
    st.markdown(f"""
- **Dataset:** DataCo Smart Supply Chain for Big Data Analysis (Constante, Silva & Pereira, 2019),
  180,519 order-item rows, Jan 2015 – Jan 2018, obtained as a public CSV mirror (see README).
- **KPI targets** (On-Time Delivery 85%, Late Delivery 15%, Avg Shipping Days ≤3.5,
  Cancelled/Fraud ≤3%, Profit Margin ≥10%) are **assumed** operational targets for this case study —
  the dataset carries no official SLA charter. All are declared in `config/config.yaml`.
- **RCA baseline window:** trailing {CFG['rca']['baseline_months']} complete months;
  **current window:** most recent {CFG['rca']['current_months']} complete month(s).
- **Contribution analysis:** for each segment, `excess = current_late_count − (baseline_rate_for_that_segment × current_volume)`.
  This isolates a *rate* effect (the segment got worse) from a pure *volume/mix* effect.
- **Significance testing:** two-proportion z-test, α = {CFG['rca']['significance_alpha']}. This flags
  statistical association only — **not proof of causation**. Findings are phrased as
  "primary contributor" / "candidate operational driver."
- **SPC p-chart:** center line and ±3σ control limits fit on the first N weeks of data, applied to
  the full series to separate common-cause noise from special-cause signals.
- **Explicitly NOT computed:** warehouse picking time, dock-to-stock time, inventory turns —
  the dataset has no warehouse-operations timestamps to support these metrics.
""")
