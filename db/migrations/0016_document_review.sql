-- Fix 4: people review what the machines decided. Machine decisions, human reviews, and learned
-- corrections are separate tables with separate writers, and the human tables are append-only.

-- The routing rule's decision for each field, written by the engine (one row per document, field, rule version).
CREATE TABLE field_decision (
    source_document_id BIGINT NOT NULL REFERENCES source_document,
    field_name      TEXT NOT NULL,
    policy_version  TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('accepted', 'review')),
    value           TEXT,
    reason          TEXT NOT NULL,
    candidates      JSONB NOT NULL DEFAULT '{}',      -- reader -> value, as the reviewer should see them
    decided_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_document_id, field_name, policy_version)
);

-- What a person did with a field. Never edited: a second opinion is a new row.
CREATE TABLE field_review (
    field_review_id BIGSERIAL PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES source_document,
    field_name      TEXT NOT NULL,
    policy_version  TEXT NOT NULL,
    action          TEXT NOT NULL CHECK (action IN ('approve', 'correct')),
    machine_status  TEXT NOT NULL,
    machine_value   TEXT,
    reviewed_value  TEXT,                              -- canonical; NULL means "not on this document"
    reviewer        TEXT NOT NULL,
    note            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (source_document_id, field_name, policy_version) REFERENCES field_decision
);
CREATE TRIGGER field_review_append_only BEFORE UPDATE OR DELETE ON field_review
    FOR EACH ROW EXECUTE FUNCTION forbid_audit_mutation();

-- A correction is remembered: the readers' values next to the right answer, so it can become a
-- regression case (the machine must never again accept the wrong value on this document).
CREATE TABLE mapping_memory (
    mapping_memory_id BIGSERIAL PRIMARY KEY,
    field_review_id BIGINT NOT NULL UNIQUE REFERENCES field_review,
    source_document_id BIGINT NOT NULL REFERENCES source_document,
    field_name      TEXT NOT NULL,
    machine_value   TEXT,
    candidates      JSONB NOT NULL DEFAULT '{}',
    correct_value   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER mapping_memory_append_only BEFORE UPDATE OR DELETE ON mapping_memory
    FOR EACH ROW EXECUTE FUNCTION forbid_audit_mutation();

GRANT SELECT, INSERT ON field_decision TO cpgos_engine;
GRANT SELECT ON field_decision, field_review, mapping_memory TO cpgos_agent, cpgos_reviewer;
GRANT INSERT ON field_review, mapping_memory TO cpgos_reviewer;
GRANT USAGE ON SEQUENCE field_review_field_review_id_seq, mapping_memory_mapping_memory_id_seq TO cpgos_reviewer;
