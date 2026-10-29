-- 0001: provenance backbone.
-- Every row that enters the system is tied to the exact dataset file and
-- ingestion run that produced it, so any number can be traced to its source.

CREATE TABLE dataset_version (
    dataset_version_id  BIGSERIAL PRIMARY KEY,
    dataset_key         TEXT NOT NULL,              -- e.g. 'm5', 'docile', 'kehe_deduction_codes'
    file_name           TEXT NOT NULL,
    sha256              CHAR(64) NOT NULL,
    license             TEXT NOT NULL,
    data_origin         TEXT NOT NULL CHECK (data_origin IN ('real', 'real_template_generated', 'real_anchored_generated', 'test_fixture')),
    registered_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (dataset_key, sha256)
);

CREATE TABLE ingestion_run (
    run_id              BIGSERIAL PRIMARY KEY,
    dataset_version_id  BIGINT NOT NULL REFERENCES dataset_version,
    contract_name       TEXT NOT NULL,
    contract_version    TEXT NOT NULL,
    code_version        TEXT NOT NULL,              -- git SHA of the loader
    status              TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'rejected', 'failed')),
    rows_in             INTEGER,
    rows_loaded         INTEGER,
    rows_quarantined    INTEGER,
    failure_reason      TEXT,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ
);

-- Bad rows are kept, never silently dropped, so data quality is auditable.
CREATE TABLE quarantined_row (
    quarantined_row_id  BIGSERIAL PRIMARY KEY,
    run_id              BIGINT NOT NULL REFERENCES ingestion_run,
    row_number          INTEGER NOT NULL,
    raw                 JSONB NOT NULL,
    violations          JSONB NOT NULL
);

CREATE TABLE source_document (
    source_document_id  BIGSERIAL PRIMARY KEY,
    sha256              CHAR(64) NOT NULL UNIQUE,   -- same file uploaded twice = same document
    source_type         TEXT NOT NULL CHECK (source_type IN (
                            'invoice', 'remittance_advice', 'deduction_notice',
                            'promotion_agreement', 'retailer_report', 'sales_file', 'other')),
    storage_key         TEXT NOT NULL,
    mime_type           TEXT,
    dataset_version_id  BIGINT REFERENCES dataset_version,
    layout_cluster      TEXT,                       -- e.g. DocILE cluster id; drives per-layout eval
    ingestion_status    TEXT NOT NULL DEFAULT 'received' CHECK (ingestion_status IN (
                            'received', 'extracted', 'validated', 'needs_review', 'rejected')),
    received_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A pointer from an extracted value back to exactly where it came from.
CREATE TABLE evidence_reference (
    evidence_id         BIGSERIAL PRIMARY KEY,
    source_document_id  BIGINT REFERENCES source_document,
    dataset_version_id  BIGINT REFERENCES dataset_version,
    page_number         INTEGER,
    row_number          INTEGER,
    bbox                JSONB,                      -- [x0, y0, x1, y1] normalized, when known
    field_name          TEXT NOT NULL,
    extracted_value     TEXT,
    confidence          NUMERIC(5,4) CHECK (confidence BETWEEN 0 AND 1),
    extractor_version   TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (source_document_id IS NOT NULL OR dataset_version_id IS NOT NULL)
);
