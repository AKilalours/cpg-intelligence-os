-- Phase 2a: evidence for deduction validation, and the validator's output.

-- Which store a claim is about. Scan-back and spoils claims are per store;
-- shortage and late-fee claims inherit the store of the invoice they cite.
ALTER TABLE deduction ADD COLUMN store_id BIGINT REFERENCES store;

-- Retailer point-of-sale units per store, SKU, and ISO week: the "proof of
-- performance" a scan-back claim is checked against. Kept apart from
-- sales_observation on purpose: Walmart weeks start on Saturday and ISO weeks
-- on Monday, and mixing the two in one series would double count.
CREATE TABLE retailer_pos_week (
    store_id        BIGINT NOT NULL REFERENCES store,
    sku_id          BIGINT NOT NULL REFERENCES product,
    week_start      DATE NOT NULL CHECK (extract(isodow FROM week_start) = 1),
    units           INTEGER NOT NULL CHECK (units >= 0),
    dataset_version_id BIGINT NOT NULL REFERENCES dataset_version,
    PRIMARY KEY (store_id, sku_id, week_start)
);

-- One row per validator run per deduction. Append-only: a new engine version
-- writes a new row; the latest row per deduction is the current view.
CREATE TABLE deduction_assessment (
    assessment_id   BIGSERIAL PRIMARY KEY,
    deduction_id    BIGINT NOT NULL REFERENCES deduction,
    engine_version  TEXT NOT NULL,
    verdict         TEXT NOT NULL CHECK (verdict IN ('valid', 'invalid', 'partially_valid', 'needs_evidence')),
    rule            TEXT NOT NULL,                  -- which check decided it
    supported_amount NUMERIC(14,2),                 -- what the evidence supports
    recoverable_amount NUMERIC(14,2) NOT NULL CHECK (recoverable_amount >= 0),
    evidence        JSONB NOT NULL,                 -- record ids and values used
    calculation_id  BIGINT REFERENCES calculation,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX deduction_assessment_latest ON deduction_assessment (deduction_id, assessment_id DESC);

-- The shared guard named audit_event in its message even when it blocked another
-- table. Name the table that was actually protected.
CREATE OR REPLACE FUNCTION forbid_audit_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only (% blocked)', TG_TABLE_NAME, TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER deduction_assessment_append_only
    BEFORE UPDATE OR DELETE ON deduction_assessment
    FOR EACH ROW EXECUTE FUNCTION forbid_audit_mutation();

-- Grants made in earlier migrations cover only tables that existed then.
GRANT SELECT ON retailer_pos_week, deduction_assessment TO cpgos_engine, cpgos_agent, cpgos_reviewer;
GRANT INSERT ON deduction_assessment TO cpgos_engine;
GRANT USAGE ON SEQUENCE deduction_assessment_assessment_id_seq TO cpgos_engine;

-- Evaluation runs, stored with what they were run on. Lives in eval: the engine
-- role cannot read it, the API reads it to show measured results.
CREATE TABLE eval.report (
    report_id       BIGSERIAL PRIMARY KEY,
    kind            TEXT NOT NULL,
    dataset_version_id BIGINT REFERENCES dataset_version,
    code_version    TEXT NOT NULL,
    body            JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
