-- =============================================================================
-- Supply Chain Control Tower — SQLite Analytical Schema
-- =============================================================================
-- Single fact table (order-item grain, matching the source data grain) plus
-- a lightweight star-ish set of derived columns to keep KPI/RCA SQL simple.
-- We use one wide fact table rather than a normalized star schema because:
--   (a) the source data has no separate dimension keys to normalize against,
--   (b) analytical (OLAP-style) workloads on ~180K rows are fast either way,
--   (c) it keeps the SQL in this case study readable for review.
-- =============================================================================

DROP TABLE IF EXISTS fact_order_items;

CREATE TABLE fact_order_items (
    order_item_id        INTEGER PRIMARY KEY,
    order_id              INTEGER NOT NULL,
    order_date             TEXT NOT NULL,       -- ISO datetime
    shipping_date          TEXT NOT NULL,       -- ISO datetime
    order_period            TEXT NOT NULL,       -- YYYY-MM
    order_week               TEXT NOT NULL,       -- ISO week label

    order_type               TEXT,                -- payment Type (DEBIT/TRANSFER/etc.)
    order_status              TEXT NOT NULL,
    delivery_status            TEXT NOT NULL,
    shipping_mode                TEXT NOT NULL,
    is_late                       INTEGER NOT NULL,   -- 1/0, from Late_delivery_risk
    is_on_time                     INTEGER NOT NULL,
    is_cancelled                    INTEGER NOT NULL,
    days_for_shipping_real            INTEGER,
    days_for_shipment_scheduled        INTEGER,
    shipping_delay_days                 INTEGER,       -- real - scheduled

    market                                TEXT,
    order_region                          TEXT,
    order_country                         TEXT,
    order_state                           TEXT,
    order_city                            TEXT,

    customer_id                           INTEGER,
    customer_segment                      TEXT,

    category_name                         TEXT,
    department_name                       TEXT,
    product_name                          TEXT,

    order_item_quantity                   INTEGER,
    sales                                  REAL,
    order_item_total                       REAL,
    order_profit_per_order                 REAL,
    sales_per_customer                     REAL,
    order_item_discount                    REAL
);

CREATE INDEX idx_fact_order_period ON fact_order_items(order_period);
CREATE INDEX idx_fact_order_week ON fact_order_items(order_week);
CREATE INDEX idx_fact_region ON fact_order_items(order_region);
CREATE INDEX idx_fact_market ON fact_order_items(market);
CREATE INDEX idx_fact_shipmode ON fact_order_items(shipping_mode);
CREATE INDEX idx_fact_category ON fact_order_items(category_name);
CREATE INDEX idx_fact_segment ON fact_order_items(customer_segment);
CREATE INDEX idx_fact_status ON fact_order_items(order_status);
