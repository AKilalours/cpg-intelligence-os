-- Phase 2b fix 2: real receipt photographs (CORD v2) with line items.
-- Answer keys live in eval, which the engine role cannot read.

ALTER TABLE source_document DROP CONSTRAINT source_document_source_type_check;
ALTER TABLE source_document ADD CONSTRAINT source_document_source_type_check CHECK (source_type IN (
    'invoice', 'remittance_advice', 'deduction_notice', 'promotion_agreement', 'retailer_report',
    'sales_file', 'receipt', 'other'));

CREATE TABLE eval.receipt_meta (
    source_document_id BIGINT PRIMARY KEY REFERENCES source_document,
    dataset_key     TEXT NOT NULL,
    doc_key         TEXT NOT NULL,                  -- "<split>/<image_id>", as published
    split           TEXT NOT NULL CHECK (split IN ('train', 'validation', 'test')),
    width           INTEGER NOT NULL,
    height          INTEGER NOT NULL,
    UNIQUE (dataset_key, doc_key)
);

-- Totals block: subtotal, discount, tax, service, total, cash, change, ...
CREATE TABLE eval.receipt_gold (
    gold_set_id     BIGINT NOT NULL REFERENCES eval.gold_set,
    source_document_id BIGINT NOT NULL REFERENCES source_document,
    field_name      TEXT NOT NULL,
    raw_text        TEXT NOT NULL,                  -- as labelled
    value           TEXT,                           -- canonical rupiah or count; NULL when it cannot be parsed
    bbox            JSONB,                          -- [x0, y0, x1, y1] in [0, 1], top-left origin
    PRIMARY KEY (gold_set_id, source_document_id, field_name)
);

-- One row per purchased line, in printed order.
CREATE TABLE eval.receipt_item_gold (
    gold_set_id     BIGINT NOT NULL REFERENCES eval.gold_set,
    source_document_id BIGINT NOT NULL REFERENCES source_document,
    item_index      INTEGER NOT NULL,
    name            TEXT,
    quantity        NUMERIC,
    unit_price      NUMERIC,
    price           NUMERIC,                        -- line amount as printed
    discount        NUMERIC,
    sub_items       JSONB NOT NULL DEFAULT '[]',    -- add-ons printed under the line
    raw             JSONB NOT NULL,                 -- the label exactly as published
    PRIMARY KEY (gold_set_id, source_document_id, item_index)
);
