-- Fix for a circular test: deduction evidence can now be incomplete, as in real files.
ALTER TABLE shipment ALTER COLUMN delivered_date DROP NOT NULL;       -- delivered, date not keyed

-- Which evidence-damage version was applied to which world (once each).
CREATE TABLE finance_evidence (
    dataset_version_id BIGINT NOT NULL REFERENCES dataset_version,
    version         TEXT NOT NULL,
    report          JSONB NOT NULL,
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset_version_id, version)
);

-- Every individual change, hidden from the engine: lets scoring tell a
-- checker mistake from a gap in the evidence it was given.
CREATE TABLE eval.gt_evidence (
    dataset_version_id BIGINT NOT NULL REFERENCES dataset_version,
    kind            TEXT NOT NULL,
    ref             TEXT NOT NULL,
    detail          JSONB NOT NULL DEFAULT '{}'
);

GRANT SELECT ON finance_evidence TO cpgos_engine, cpgos_agent, cpgos_reviewer;
