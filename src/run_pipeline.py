"""
Pipeline orchestrator — run the full flow end to end:
  validate -> clean -> load SQLite -> KPI -> RCA -> Pareto/SPC -> exec report

Usage: python -m src.run_pipeline
"""
import sys

from src.common import get_logger
from src.etl_load import build_database
from src.kpi_engine import compute_kpis, detect_breaches
from src.rca_engine import run_rca
from src.pareto_spc import pareto_by_dimension, spc_weekly_late_rate
from src.recommendations import generate_recommendations
from src.exec_report import generate_exec_report

logger = get_logger(__name__)


def main():
    logger.info("=== STEP 1/6: Validate + Clean + Load SQLite ===")
    build_database(force=True)

    logger.info("=== STEP 2/6: KPI Engine ===")
    monthly = compute_kpis("monthly")
    breaches = detect_breaches(monthly)
    logger.info("%d periods computed, %d breaches detected.", len(monthly), len(breaches))

    logger.info("=== STEP 3/6: RCA Engine ===")
    rca = run_rca()
    logger.info("RCA complete: %s", rca.narrative[0])

    logger.info("=== STEP 4/6: Pareto + SPC ===")
    pareto_by_dimension("order_region")
    spc = spc_weekly_late_rate()
    logger.info("SPC: %d/%d weeks out of control.", spc["n_out_of_control"], len(spc["series"]))

    logger.info("=== STEP 5/6: Recommendations ===")
    recs = generate_recommendations(rca)
    logger.info("%d recommendations generated.", len(recs))

    logger.info("=== STEP 6/6: Executive Report ===")
    generate_exec_report()

    logger.info("Pipeline complete. Run 'streamlit run dashboard/app.py' to view the dashboard.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Pipeline failed.")
        sys.exit(1)
