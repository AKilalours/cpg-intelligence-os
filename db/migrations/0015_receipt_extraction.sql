-- Fix 2, step 2: what each reader read from a receipt photo (items and totals), as the engine wrote it.
-- One row per receipt and reader version; the body keeps the model's printed text and the parsed values.

CREATE TABLE receipt_extraction (
    receipt_extraction_id BIGSERIAL PRIMARY KEY,
    source_document_id BIGINT NOT NULL REFERENCES source_document,
    extractor_version TEXT NOT NULL,                -- prompt + provider:model@digest + image rendering
    reader          TEXT NOT NULL,                  -- 'local_model' | 'claude'
    body            JSONB,                          -- {"fields": {...}, "items": [...], "printed": {...}}; NULL if the call failed
    call            JSONB NOT NULL,                 -- seconds, tokens, recording key, error
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_document_id, extractor_version)
);

GRANT SELECT, INSERT ON receipt_extraction TO cpgos_engine;
GRANT USAGE ON SEQUENCE receipt_extraction_receipt_extraction_id_seq TO cpgos_engine;
GRANT SELECT ON receipt_extraction TO cpgos_agent, cpgos_reviewer;
