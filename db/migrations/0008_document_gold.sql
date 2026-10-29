-- Phase 2b: answer keys for document extraction. Lives in eval, which the
-- engine role cannot read, so no extractor or router can see the answers.

-- A versioned answer key: the same dataset normalised by different code is a
-- different gold set, so a score always names exactly what it was scored on.
CREATE TABLE eval.gold_set (
    gold_set_id     BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    dataset_version_id BIGINT NOT NULL REFERENCES dataset_version,
    gold_version    TEXT NOT NULL,                  -- hash of the normalisation code
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name, dataset_version_id, gold_version)
);

-- Per document: the true layout and its split. Kept out of
-- source_document.layout_cluster on purpose, because the engine can read that.
CREATE TABLE eval.doc_meta (
    source_document_id BIGINT PRIMARY KEY REFERENCES source_document,
    dataset_key     TEXT NOT NULL,
    doc_key         TEXT NOT NULL,
    layout_id       INTEGER NOT NULL,
    split_seen      TEXT CHECK (split_seen IN ('train', 'dev', 'test')),          -- same layouts everywhere
    split_unseen    TEXT NOT NULL CHECK (split_unseen IN ('fit', 'calibration', 'holdout')),
    UNIQUE (dataset_key, doc_key)
);

CREATE TABLE eval.doc_gold (
    gold_set_id     BIGINT NOT NULL REFERENCES eval.gold_set,
    source_document_id BIGINT NOT NULL REFERENCES source_document,
    field_name      TEXT NOT NULL,
    raw_text        TEXT NOT NULL,                  -- as printed, label included
    value           TEXT NOT NULL,                  -- canonical: ISO date, "734.33", "USD", ...
    bbox            JSONB,                          -- [x0, y0, x1, y1] in [0, 1], top-left origin
    PRIMARY KEY (gold_set_id, source_document_id, field_name)
);
