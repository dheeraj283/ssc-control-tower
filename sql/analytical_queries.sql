-- =============================================================================
-- Analytical SQL query library
-- These are reference queries showing the same logic the Python KPI/RCA
-- engines execute via pandas.read_sql. Kept here for transparency/review
-- and for direct use in a SQL client (DB Browser for SQLite, etc.)
-- =============================================================================

-- 1. Monthly KPI summary -------------------------------------------------
SELECT
    order_period,
    COUNT(*)                                            AS order_items,
    COUNT(DISTINCT order_id)                            AS orders,
    ROUND(100.0 * SUM(is_on_time) / COUNT(*), 2)        AS on_time_delivery_pct,
    ROUND(100.0 * SUM(is_late) / COUNT(*), 2)           AS late_delivery_pct,
    ROUND(AVG(days_for_shipping_real), 2)               AS avg_shipping_days,
    ROUND(100.0 * SUM(is_cancelled) / COUNT(*), 2)       AS cancelled_pct,
    ROUND(SUM(order_profit_per_order), 2)                AS total_profit,
    ROUND(100.0 * SUM(order_profit_per_order) / NULLIF(SUM(sales_per_customer),0), 2) AS profit_margin_pct
FROM fact_order_items
GROUP BY order_period
ORDER BY order_period;

-- 2. Late-delivery rate by dimension (for a given period), used by RCA ---
SELECT
    order_region,
    COUNT(*)                                     AS n_orders,
    SUM(is_late)                                  AS n_late,
    ROUND(100.0 * SUM(is_late) / COUNT(*), 2)     AS late_pct
FROM fact_order_items
WHERE order_period = '2017-07'
GROUP BY order_region
HAVING COUNT(*) >= 30
ORDER BY late_pct DESC;

-- 3. Pareto contribution of shipping mode to total late deliveries -------
WITH late_by_mode AS (
    SELECT shipping_mode, COUNT(*) AS n_late
    FROM fact_order_items
    WHERE is_late = 1
    GROUP BY shipping_mode
)
SELECT
    shipping_mode,
    n_late,
    ROUND(100.0 * n_late / SUM(n_late) OVER (), 2)                                   AS pct_of_total_late,
    ROUND(100.0 * SUM(n_late) OVER (ORDER BY n_late DESC) / SUM(n_late) OVER (), 2)   AS cumulative_pct
FROM late_by_mode
ORDER BY n_late DESC;

-- 4. Weekly SPC series (proportion late per week) -------------------------
SELECT
    order_week,
    COUNT(*)                                  AS n,
    SUM(is_late)                              AS n_late,
    ROUND(1.0 * SUM(is_late) / COUNT(*), 4)   AS p_late
FROM fact_order_items
GROUP BY order_week
ORDER BY order_week;

-- 5. Order status funnel (cancellations / fraud flags) ---------------------
SELECT
    order_status,
    COUNT(*)                                                AS n,
    ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM fact_order_items), 2) AS pct_of_all
FROM fact_order_items
GROUP BY order_status
ORDER BY n DESC;
