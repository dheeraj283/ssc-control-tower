"""
Corrective-Action Recommendation Engine
========================================
Transparent, rule-based mapping from RCA/Pareto findings to recommended
operational actions. NOT a machine-learning model — every rule is a plain
if/then business heuristic so it can be inspected, challenged, and edited
by an ops analyst. This mirrors how Lean/Six-Sigma corrective-action logs
are typically built in industry.

Each recommendation includes:
  - trigger (what pattern in the data fired it)
  - action (what to do)
  - owner (which function should own it)
  - priority (High/Medium/Low, driven by contribution_pct + significance)
"""
from dataclasses import dataclass

from src.rca_engine import RcaResult, SegmentContribution


@dataclass
class Recommendation:
    priority: str
    trigger: str
    action: str
    owner: str
    rationale: str


DIMENSION_ACTION_MAP = {
    "shipping_mode": {
        "owner": "Logistics / Carrier Management",
        "action_template": (
            "Audit carrier performance and transit-time commitments for shipping mode "
            "'{segment}'. If the current rate ({current}%) is a statistically significant "
            "regression vs baseline ({baseline}%), open a carrier SLA review; if not "
            "significant, monitor for one more period before escalating."
        ),
    },
    "order_region": {
        "owner": "Regional Fulfillment Ops",
        "action_template": (
            "Review fulfillment-center capacity and last-mile carrier coverage for region "
            "'{segment}'. Check for cross-border customs delays if the region is "
            "inter-continental relative to the shipping origin."
        ),
    },
    "market": {
        "owner": "Regional Fulfillment Ops",
        "action_template": (
            "Review network design (DC-to-market routing) for market '{segment}'; "
            "confirm whether volume growth outpaced allocated shipping capacity."
        ),
    },
    "category_name": {
        "owner": "Category / Supplier Management",
        "action_template": (
            "Investigate supplier/vendor lead-time reliability for category "
            "'{segment}'. Check for SKU-level stockouts or bulky/oversized-item "
            "handling exceptions that could inflate processing time."
        ),
    },
    "customer_segment": {
        "owner": "Commercial / Sales Ops",
        "action_template": (
            "Review order profile for customer segment '{segment}' (order size, "
            "payment terms, geographic concentration) to see if it structurally "
            "correlates with slower fulfillment paths."
        ),
    },
    "order_status": {
        "owner": "Order Management",
        "action_template": (
            "Review workflow delay for orders in status '{segment}'. If "
            "PENDING_PAYMENT or ON_HOLD dominate, the delay may originate in "
            "payment/fraud review rather than physical fulfillment — coordinate "
            "with Finance/Risk before flagging Logistics."
        ),
    },
}


def _priority(seg: SegmentContribution) -> str:
    if seg.contribution_pct >= 40 and seg.significant:
        return "High"
    if seg.contribution_pct >= 15:
        return "Medium"
    return "Low"


def generate_recommendations(rca: RcaResult, max_per_dimension: int = 2) -> list[Recommendation]:
    """Turn RCA findings into a prioritized, transparent action list."""
    recs: list[Recommendation] = []

    for dim, segs in rca.dimension_results.items():
        mapping = DIMENSION_ACTION_MAP.get(dim)
        if not mapping:
            continue
        for seg in [s for s in segs if s.contribution_pct > 0][:max_per_dimension]:
            action = mapping["action_template"].format(
                segment=seg.segment,
                current=seg.current_rate_pct,
                baseline=seg.baseline_rate_pct if seg.baseline_rate_pct is not None else "n/a",
            )
            recs.append(Recommendation(
                priority=_priority(seg),
                trigger=(
                    f"{dim} = '{seg.segment}' contributed {seg.contribution_pct}% of excess "
                    f"late orders in {rca.current_periods[-1]} "
                    f"({'statistically significant' if seg.significant else 'not yet statistically significant'})"
                ),
                action=action,
                owner=mapping["owner"],
                rationale=(
                    f"Current late rate {seg.current_rate_pct}% vs baseline "
                    f"{seg.baseline_rate_pct}% across {seg.current_volume} orders "
                    f"(p={seg.p_value})."
                ),
            ))

    priority_order = {"High": 0, "Medium": 1, "Low": 2}
    recs.sort(key=lambda r: priority_order[r.priority])

    if not recs:
        recs.append(Recommendation(
            priority="Low",
            trigger="No segment showed a statistically significant or materially large contribution to KPI deterioration.",
            action="Continue standard monitoring cadence; no corrective action warranted this period.",
            owner="Ops Control Tower",
            rationale="RCA engine found no dimension with contribution_pct > 0 and significant shift.",
        ))
    return recs


if __name__ == "__main__":
    from src.rca_engine import run_rca
    rca = run_rca()
    for r in generate_recommendations(rca):
        print(f"[{r.priority}] {r.trigger}\n  -> {r.action}\n  Owner: {r.owner}\n")
