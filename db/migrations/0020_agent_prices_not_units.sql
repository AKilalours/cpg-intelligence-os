-- Phase 3: the world's retailer POS is derived from the real M5 sales in sales_observation, so the
-- agent reading units there would be reading the true POS from under the damaged evidence. A
-- supplier does see shelf prices (store checks, syndicated data), and a price drop is a legitimate
-- sign that a promotion ran. The agent may read prices, never units, and uses a price drop only to
-- ask for an agreement instead of disputing, never to accept a claim.
REVOKE SELECT ON sales_observation FROM cpgos_agent;
GRANT SELECT (sales_observation_id, obs_date, granularity, sku_id, store_id, shelf_price, dataset_version_id)
    ON sales_observation TO cpgos_agent;
REVOKE SELECT ON sales_series_stats FROM cpgos_agent;   -- per-series unit totals: the same leak, summed
GRANT INSERT ON calculation TO cpgos_agent;             -- the agent's arithmetic, recorded like the engine's
GRANT USAGE ON SEQUENCE calculation_calculation_id_seq TO cpgos_agent;
