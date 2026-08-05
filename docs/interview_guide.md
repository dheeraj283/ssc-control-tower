# Interview Guide — Supply Chain Operations Control Tower

## Project in 30 seconds

"I built an end-to-end supply-chain control tower on 180K real e-commerce order
records — it computes operational KPIs like on-time delivery and SLA adherence,
automatically detects when they breach target, and runs a statistical root-cause
analysis across region, shipping mode, and product category to identify the
biggest contributors. It outputs a Streamlit dashboard and an auto-generated
executive report, the same workflow an ops analyst would run weekly."

## Project in 2 minutes

"I used the DataCo Smart Supply Chain dataset — about 180,000 order-item records
from 2015 to 2018 — and built a pipeline that mirrors what a supply-chain control
tower actually does. First, I validate and clean the raw data and load it into a
SQLite analytical layer. Then a KPI engine computes on-time delivery, late-delivery
rate, SLA adherence, shipping time, cancellation rate, and profit margin at monthly
and weekly grain, and flags anything breaching an assumed target.

The core of the project is the RCA engine. When late-delivery breaches target, it
compares a baseline period against the current period and decomposes the change
using a contribution-analysis technique — for each segment, like a shipping mode
or region, I calculate how much of the 'excess' late orders came from that
segment's own rate getting worse, versus just having more volume. I run a
two-proportion z-test on each segment to flag which shifts are statistically
significant, then do a hierarchical drill-down into the top segment with a second
dimension.

I also added Pareto analysis and an SPC control chart, because in this dataset the
late-delivery rate is chronically around 54% every month — it's not a recent spike,
it's a structural gap. The Pareto chart actually shows that finding clearly: no
single region dominates, which tells you it's a systemic issue, not a localized
one. Everything feeds into a rule-based recommendation engine — not machine
learning, deliberately, because corrective actions need to be auditable — and an
auto-generated executive Markdown report."

## Architecture Walkthrough

Raw CSV → validation/cleaning (schema checks, dedup, date parsing) → SQLite fact
table → KPI engine (SQL aggregation) → breach detection against configured targets
→ RCA engine (contribution analysis + significance testing + drill-down) →
Pareto/SPC modules → rule-based recommendation engine → executive report generator
→ Streamlit dashboard consuming all of the above. See README.md for the Mermaid
diagram.

## Every KPI, explained

- **On-Time Delivery % / Late Delivery %** — derived from the dataset's
  `Late_delivery_risk` flag (order-item level). These are complementary (sum to
  100%).
- **SLA Adherence %** — same measurement basis as On-Time Delivery; kept as a
  separately-named KPI because in a real ops role, "SLA adherence" is often the
  externally-reported metric while "on-time delivery" is the internal ops metric —
  even when computed identically, they get tracked separately for stakeholder
  communication reasons.
- **Avg Shipping Days** — mean of `Days for shipping (real)`, the actual transit
  time realized.
- **Cancelled/Fraud %** — share of orders in `CANCELED` or `SUSPECTED_FRAUD`
  status — a proxy for order-quality/trust issues upstream of fulfillment.
- **Profit Margin %** — `Order Profit Per Order / Sales per customer` — included
  because the role scope explicitly mentions cost, and this is the one
  profitability signal genuinely supported by the data.

**Deliberately not computed:** warehouse picking time, dock-to-stock time,
inventory turns. If asked why, the honest answer: this dataset has no
warehouse-floor timestamps, only order → ship → deliver level data, and inventing
those numbers would misrepresent the analysis.

## RCA Methodology — deeper dive

**Contribution analysis formula:**
```
excess_segment = current_late_count_segment − (segment's_own_baseline_rate × current_volume_segment)
contribution_% = excess_segment / sum(all positive excess) × 100
```
Using the segment's *own* baseline rate (not the overall baseline rate) isolates a
rate effect — did this specific segment's performance change — from a pure
mix/volume effect, where a segment just processed more orders at its usual rate.

**Why a two-proportion z-test:** it's the standard test for comparing two
independent proportions (baseline rate vs current rate) and is appropriate at the
sample sizes here (thousands of orders per segment). I set α = 0.05 and always
report both the direction of the effect and whether it clears significance —
because a large contribution_% with no significance is a **weaker** signal than a
smaller but significant one, and I don't want the dashboard to overstate
confidence.

**Why "candidate driver" language, not "root cause":** the dataset is
observational — I'm looking at order outcomes, not the underlying operational
events (a carrier capacity constraint, a warehouse staffing gap, a customs delay).
Correlation between "Shipping Mode = Same Day" and a higher late rate is a strong
lead for an investigation, not proof that the shipping mode itself causes the
lateness — it could, for instance, correlate with a specific fulfillment center
that's independently under strain.

## Pareto — why it mattered here

Classic Pareto expectation is 80/20 — a few segments driving most of the problem.
In this dataset, by region, it takes **11 of 15 regions to reach 80%** of total
late orders — a flat, non-concentrated distribution. That's a meaningful negative
result: it tells you the fix isn't "go investigate Region X," it's "look at
something shared across the whole network" (e.g., how scheduled-day estimates are
calculated, or a shared carrier issue). Knowing when Pareto *doesn't* show
concentration is as useful as knowing when it does.

## SPC — why it mattered here

The p-chart center line sits around 54%, with only 16 of 162 weeks falling outside
±3σ control limits. That tells you the late-delivery problem is **common-cause,
not special-cause** — it's baked into the process every week, not the result of
occasional disruptions. That reframes the fix: this isn't a "find the bad week"
problem, it's a "redesign the process/estimate" problem.

## Why SQL / SQLite

I used SQL for the KPI and RCA aggregations because that's the tool used in
production data warehouses for exactly this kind of grouped, filtered aggregation
— `GROUP BY` period, `HAVING` a minimum sample size, window functions for
cumulative Pareto percentages. I used SQLite specifically (over Postgres/MySQL) so
the whole project runs locally with zero setup for a reviewer, while keeping the
SQL itself portable to any real warehouse.

## Assumptions (be ready to state these plainly)

1. KPI targets (85% on-time, 15% late, 3.5 days avg shipping, 3% cancellation, 10%
   margin) are **assumed**, since the dataset has no SLA charter.
2. Baseline window = trailing 3 complete months; current = most recent 1 complete
   month. Both are configurable.
3. Minimum segment size of 30 orders to avoid small-sample rate noise.
4. Significance threshold α = 0.05.
5. The last 4 months of the raw file are treated as an incomplete/truncated
   artifact and excluded from trend/RCA analysis (documented, measured, not
   guessed).

## Limitations (be ready to state these plainly)

- Findings are associations, not proven causes.
- No warehouse-floor or carrier-level operational data — can't drill below
  order-item grain.
- Historical data (through Jan 2018), not a live feed.
- KPI targets are illustrative assumptions, not a real SLA charter.

## What would change with real enterprise (e.g. Flipkart-scale) data

- Targets would come from the actual SLA charter per business line (FC, City
  Logistics, Grocery) instead of being assumed.
- I'd have warehouse-floor timestamps (pick, pack, dispatch) enabling genuine
  process-step KPIs, not just order-to-delivery.
- I'd have carrier-level and route-level data to test the "Same Day mode" finding
  against actual carrier capacity/exception logs — moving from association toward
  causal confidence.
- Volume would likely require moving from SQLite to a real warehouse (BigQuery/
  Snowflake) with incremental loads via Airflow/dbt, and breach alerts would go to
  Slack/email in near-real-time instead of a batch report.
- I'd validate the "no 80/20 concentration" Pareto finding against a longer history
  to confirm it's not itself an artifact of this particular data window.

## 20 Likely Interview Questions & Strong Answers

**1. Walk me through this project.**
→ Use the "2 minutes" answer above.

**2. Why did you choose this dataset?**
→ It's real e-commerce order-level data with delivery-status, shipping-mode,
region, and category fields — genuinely supports the KPI/RCA methodology this role
needs, unlike a purely synthetic dataset that risks looking contrived.

**3. What was the single most interesting finding?**
→ That late delivery is chronically ~54% every month for the entire 3-year window,
and Pareto shows no regional concentration — meaning this isn't a "find the bad
region" problem, it's a systemic process-design problem, likely in how scheduled
delivery estimates are set.

**4. How do you know your RCA isn't just noise?**
→ I run a two-proportion z-test per segment and explicitly flag which shifts clear
statistical significance (α=0.05), and I report contribution_% and significance
together rather than treating a large contribution alone as proof.

**5. Why not just use correlation/regression for RCA?**
→ I do use a form of decomposition (contribution analysis) plus a formal
significance test; I avoided fitting a predictive model because the goal here is
transparent, explainable attribution an ops team can act on immediately, not a
black-box prediction.

**6. How would you validate the "Same Day shipping mode" finding operationally?**
→ Pull carrier-level SLA logs for Same Day shipments in the current vs baseline
window, check for a specific onboarding/capacity change, and if possible run a
natural-experiment style before/after comparison isolating that one change.

**7. What's the difference between contribution_% and the z-test significance?**
→ Contribution_% measures *how much* of the total excess a segment explains (an
effect-size / Pareto-style measure). The z-test measures whether that segment's
rate shift is distinguishable from random noise given its sample size. A segment
can have high contribution but low significance if its sample size is small.

**8. Why exclude the last 4 months of data?**
→ I measured the order-item-to-order ratio per month and found it collapses from
~3.0 to ~1.0 in the trailing months — a known artifact suggesting incomplete
export, not real behavior. Including it would have produced a false "improvement"
signal purely from missing line-items, so I excluded it and documented why.

**9. How do you decide what counts as a "target"?**
→ I document them as assumptions in `config/config.yaml`, since the dataset has no
SLA charter. In a real role I'd pull them from the actual Ops/CX SLA agreement.

**10. What's your SQL doing vs your Python doing?**
→ SQL does the grouped aggregation (KPI-by-period, late-rate-by-segment). Python
does the statistical layer (z-tests, contribution decomposition, Pareto
cumulative-%, control-limit math) and orchestration/presentation.

**11. Why SQLite instead of Postgres?**
→ Zero-setup reproducibility for a reviewer; the SQL itself is standard and
portable to any RDBMS if this were productionized.

**12. How would this scale to millions of orders / real-time data?**
→ Swap SQLite for a warehouse, batch-load via Airflow/dbt on a schedule (or stream
via Kafka + a materialized view for near-real time), and move breach detection to
trigger alerts directly rather than a pulled report.

**13. What's a p-chart and why did you use one?**
→ A p-chart is an SPC tool for monitoring a proportion (like a late-delivery rate)
over time against control limits derived from its own historical variation. I used
it to separate "the process is chronically bad but stable" from "something just
changed" — which is exactly what I found: mostly stable, chronically bad.

**14. What would you do differently with more time?**
→ Add a causal-inference layer around known operational changes, add real carrier/
warehouse data, and add a lightweight cost-impact estimate per recommended action
to support explicit ROI-based prioritization.

**15. How did you handle data quality issues?**
→ Explicit validation (required-column check, that fails loudly if missing),
row-level cleaning (dedup, non-positive quantity removal, date parsing failures
dropped and logged), and a persisted data-quality report surfaced directly in the
dashboard's Data Quality tab — nothing is silently dropped or fixed without a log
line.

**16. Why phrase findings as "candidate driver" instead of "root cause"?**
→ Because I only have observational order outcomes, not the underlying process
events. Calling something a proven root cause without process/system evidence
would overstate what the statistics actually support.

**17. What's the business impact of your "no Pareto concentration" finding?**
→ It redirects investigation effort. If late deliveries were concentrated in one
region, you'd fix that region. Since they're spread across 11+ regions
proportionally to volume, the fix likely needs to happen once, network-wide (e.g.,
recalibrating how "Days for shipment (scheduled)" targets are set), which is a
much higher-leverage, lower-effort fix than 11 separate regional interventions.

**18. How did you test your code?**
→ Unit tests on the KPI breach-detection math (both directions: KPIs where
higher-is-worse and lower-is-worse) and the RCA contribution/significance math,
using small synthetic datasets with a known, engineered effect so the tests assert
against a ground-truth answer, not just "it runs."

**19. What's the hardest part of this project?**
→ Deciding how to phrase RCA output honestly — it would have been easy to write
"Region X causes late deliveries," which is both more exciting and wrong. Getting
the contribution-analysis math right (separating rate effect from volume/mix
effect) and then being disciplined about the causal language throughout the
dashboard and reports was the actual engineering-plus-judgment work.

**20. Why does this matter for an Assistant Manager, NEEV role specifically?**
→ The role is explicitly about monitoring KPIs, running RCA, and driving process
improvement with Lean methodology. This project is a working demonstration of that
exact loop — KPI breach → RCA → Pareto/SPC → prioritized corrective action →
executive communication — built on real data, not a slide deck.
