-- 0005: per-series summary, maintained at load time.
-- Pages used to aggregate all of sales_observation (6.7M weekly rows for M5)
-- on every request: 7-8 s for Planning on a laptop. Loads are rare and page
-- views are frequent, so the aggregation now happens once per load.
CREATE MATERIALIZED VIEW sales_series_stats AS
SELECT sku_id, store_id, granularity,
       count(*)      AS observations,
       sum(units)    AS total_units,
       min(obs_date) AS first_date,
       max(obs_date) AS last_date
FROM sales_observation
GROUP BY sku_id, store_id, granularity;

-- Required for REFRESH ... CONCURRENTLY (readers are never blocked).
CREATE UNIQUE INDEX sales_series_stats_key ON sales_series_stats (sku_id, store_id, granularity);
CREATE INDEX sales_series_stats_top ON sales_series_stats (granularity, total_units DESC);

GRANT SELECT ON sales_series_stats TO cpgos_agent, cpgos_reviewer;
