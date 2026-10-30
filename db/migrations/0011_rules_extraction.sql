-- Phase 2b tier 1: OCR + rules. The learned rules are stored as data the engine
-- can read; every extracted field is stored with its evidence and signals.

CREATE TABLE rules_model (
    rules_model_id  BIGSERIAL PRIMARY KEY,
    version         TEXT NOT NULL,
    content         JSONB NOT NULL,                 -- field areas per layout, label vocabulary per field
    trained_on      JSONB NOT NULL,
    content_sha256  CHAR(64) NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per document, field, and extractor version. value NULL = the extractor abstained.
CREATE TABLE field_extraction (
    field_extraction_id BIGSERIAL PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES source_document,
    extractor       TEXT NOT NULL,                  -- e.g. 'rules'
    extractor_version TEXT NOT NULL,                -- rules version + rules model + OCR engine
    field_name      TEXT NOT NULL,
    value           TEXT,
    raw_text        TEXT,
    bbox            JSONB,
    path            TEXT NOT NULL,                  -- 'known_layout' | 'label_search' | ...
    confidence      NUMERIC(5,4) CHECK (confidence BETWEEN 0 AND 1),
    signals         JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_document_id, extractor_version, field_name)
);
CREATE INDEX field_extraction_doc ON field_extraction (source_document_id);

GRANT SELECT ON rules_model, field_extraction TO cpgos_engine, cpgos_agent, cpgos_reviewer;
GRANT INSERT ON field_extraction TO cpgos_engine;
GRANT USAGE ON SEQUENCE field_extraction_field_extraction_id_seq TO cpgos_engine;
