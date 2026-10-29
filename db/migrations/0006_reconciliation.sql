-- 0006: evidence records, trade terms, provenance on financial rows, and a
-- hidden ground-truth schema for evaluation.

-- Generated financial rows must say where they came from, like sales rows do.
ALTER TABLE invoice   ADD COLUMN dataset_version_id BIGINT REFERENCES dataset_version;
ALTER TABLE payment   ADD COLUMN dataset_version_id BIGINT REFERENCES dataset_version;
ALTER TABLE deduction ADD COLUMN dataset_version_id BIGINT REFERENCES dataset_version;
ALTER TABLE promotion ADD COLUMN dataset_version_id BIGINT REFERENCES dataset_version;
ALTER TABLE promotion ADD COLUMN store_id BIGINT REFERENCES store;
ALTER TABLE promotion ADD COLUMN allowance_per_unit NUMERIC(14,4);
ALTER TABLE invoice   ADD COLUMN store_id BIGINT REFERENCES store;   -- ship-to store
ALTER TABLE deduction ADD COLUMN sku_id BIGINT REFERENCES product;
ALTER TABLE deduction ADD COLUMN claimed_units NUMERIC(14,3);
ALTER TABLE deduction ADD COLUMN period_start DATE;
ALTER TABLE deduction ADD COLUMN period_end DATE;

-- Proof of delivery: what was promised vs what actually arrived and when.
CREATE TABLE shipment (
    shipment_id     BIGSERIAL PRIMARY KEY,
    invoice_id      BIGINT NOT NULL UNIQUE REFERENCES invoice,
    scheduled_date  DATE NOT NULL,
    delivered_date  DATE NOT NULL,
    dataset_version_id BIGINT REFERENCES dataset_version
);
CREATE TABLE receipt_line (
    invoice_line_id BIGINT PRIMARY KEY REFERENCES invoice_line,
    units_received  NUMERIC(14,3) NOT NULL CHECK (units_received >= 0)
);

-- Negotiated and published terms, each with where the number came from.
CREATE TABLE trade_term (
    partner_id      BIGINT NOT NULL REFERENCES trading_partner,
    term_key        TEXT NOT NULL,          -- e.g. 'late_shipment_fee', 'dispute_window_days'
    value           NUMERIC(14,4) NOT NULL,
    source          TEXT NOT NULL,          -- URL for published terms, 'assumption' otherwise
    PRIMARY KEY (partner_id, term_key)
);

-- A generated world is a snapshot "as of" its last day (M5 ends 2016-05-22).
-- Deadlines and ages are measured against this date, not the wall clock,
-- otherwise every 2016 deduction would look ten years overdue.
CREATE TABLE world_clock (
    dataset_version_id BIGINT PRIMARY KEY REFERENCES dataset_version,
    as_of_date      DATE NOT NULL
);

-- The answer key. Engines run as cpgos_engine, which cannot read this schema.
CREATE SCHEMA eval;
CREATE TABLE eval.gt_remittance_alloc (
    remittance_line_id BIGINT NOT NULL REFERENCES remittance_line,
    invoice_id      BIGINT REFERENCES invoice,     -- NULL: line pays nothing identifiable
    amount          NUMERIC(14,2) NOT NULL,
    noise           TEXT NOT NULL,                 -- 'clean', 'typo_ref', 'missing_ref', 'wrong_ref', 'partial'
    PRIMARY KEY (remittance_line_id)
);
CREATE TABLE eval.gt_deduction (
    deduction_id    BIGINT PRIMARY KEY REFERENCES deduction,
    is_valid        BOOLEAN NOT NULL,
    valid_amount    NUMERIC(14,2) NOT NULL CHECK (valid_amount >= 0),
    reason          TEXT NOT NULL                  -- e.g. 'valid', 'overclaim', 'duplicate', 'no_agreement'
);

-- Engine role: reads the ledger, records decisions, never sees eval.*
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cpgos_engine') THEN
        CREATE ROLE cpgos_engine NOLOGIN;
    END IF;
END $$;
GRANT USAGE ON SCHEMA public TO cpgos_engine;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO cpgos_engine;
GRANT INSERT ON cash_application, calculation, audit_event TO cpgos_engine;
GRANT UPDATE (status) ON invoice, deduction, cash_application TO cpgos_engine;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO cpgos_engine;
REVOKE ALL ON SCHEMA eval FROM PUBLIC;

GRANT SELECT ON shipment, receipt_line, trade_term, world_clock TO cpgos_agent, cpgos_reviewer;
