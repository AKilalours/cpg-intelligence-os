-- Evidence damage becomes an overlay. The system of record is never altered.
--
-- Fix for evidence-1.0.0, which deleted receipts and promotions and blanked
-- delivery dates and stores in the real tables: the truth was destroyed, and
-- the damage depended on database ids, so two machines damaged different rows.
--
-- Analogy: a photocopy with some pages left out. The checker gets the copy
-- (schema "observed"); the original file stays complete in the cabinet.

CREATE SCHEMA evidence;                         -- what is hidden or mis-keyed; owner only
REVOKE ALL ON SCHEMA evidence FROM PUBLIC;

CREATE TABLE evidence.pos_week (
    store_id        BIGINT NOT NULL,
    sku_id          BIGINT NOT NULL,
    week_start      DATE NOT NULL,
    reported_units  INTEGER,                    -- NULL: the week is missing from the retailer feed
    PRIMARY KEY (store_id, sku_id, week_start),
    FOREIGN KEY (store_id, sku_id, week_start) REFERENCES retailer_pos_week
);
CREATE TABLE evidence.receipt_missing (invoice_line_id BIGINT PRIMARY KEY REFERENCES receipt_line);
CREATE TABLE evidence.delivery_missing (shipment_id BIGINT PRIMARY KEY REFERENCES shipment);
CREATE TABLE evidence.promo_missing (promotion_id BIGINT PRIMARY KEY REFERENCES promotion);
CREATE TABLE evidence.promo_end (promotion_id BIGINT PRIMARY KEY REFERENCES promotion, keyed_end DATE NOT NULL);
CREATE TABLE evidence.claim_store_missing (deduction_id BIGINT PRIMARY KEY REFERENCES deduction);

-- Log rows now say which damage version made them; 2.0 refs are business keys, not ids.
ALTER TABLE eval.gt_evidence ADD COLUMN version TEXT NOT NULL DEFAULT 'evidence-1.0.0';

-- What the checker sees: the records with the overlay applied.
CREATE SCHEMA observed;

CREATE VIEW observed.retailer_pos_week AS
SELECT p.store_id, p.sku_id, p.week_start, coalesce(o.reported_units, p.units) AS units, p.dataset_version_id
FROM public.retailer_pos_week p
LEFT JOIN evidence.pos_week o ON o.store_id = p.store_id AND o.sku_id = p.sku_id AND o.week_start = p.week_start
WHERE o.store_id IS NULL OR o.reported_units IS NOT NULL;

CREATE VIEW observed.receipt_line AS
SELECT r.invoice_line_id, r.units_received
FROM public.receipt_line r
WHERE NOT EXISTS (SELECT 1 FROM evidence.receipt_missing m WHERE m.invoice_line_id = r.invoice_line_id);

CREATE VIEW observed.shipment AS
SELECT s.shipment_id, s.invoice_id, s.scheduled_date,
       CASE WHEN m.shipment_id IS NULL THEN s.delivered_date END AS delivered_date, s.dataset_version_id
FROM public.shipment s LEFT JOIN evidence.delivery_missing m ON m.shipment_id = s.shipment_id;

CREATE VIEW observed.promotion AS
SELECT p.promotion_id, p.brand_id, p.partner_id, p.promo_type, p.start_date,
       coalesce(e.keyed_end, p.end_date) AS end_date, p.discount_per_unit, p.discount_pct, p.planned_spend,
       p.source_document_id, p.dataset_version_id, p.store_id, p.allowance_per_unit
FROM public.promotion p LEFT JOIN evidence.promo_end e ON e.promotion_id = p.promotion_id
WHERE NOT EXISTS (SELECT 1 FROM evidence.promo_missing m WHERE m.promotion_id = p.promotion_id);

CREATE VIEW observed.promotion_sku AS
SELECT ps.promotion_id, ps.sku_id
FROM public.promotion_sku ps
WHERE NOT EXISTS (SELECT 1 FROM evidence.promo_missing m WHERE m.promotion_id = ps.promotion_id);

CREATE VIEW observed.deduction AS
SELECT d.deduction_id, d.claim_number, d.partner_id, d.remittance_line_id, d.deduction_code_id, d.raw_reason,
       d.amount, d.deduction_date, d.dispute_deadline, d.raw_invoice_ref, d.invoice_id, d.promotion_id, d.status,
       d.dataset_version_id, d.sku_id, d.claimed_units, d.period_start, d.period_end,
       CASE WHEN m.deduction_id IS NULL THEN d.store_id END AS store_id
FROM public.deduction d LEFT JOIN evidence.claim_store_missing m ON m.deduction_id = d.deduction_id;

GRANT USAGE ON SCHEMA observed TO cpgos_engine, cpgos_agent, cpgos_reviewer;
GRANT SELECT ON ALL TABLES IN SCHEMA observed TO cpgos_engine, cpgos_agent, cpgos_reviewer;

-- The engine may no longer read the originals of the evidence it is judged on.
REVOKE SELECT ON retailer_pos_week, receipt_line, shipment, promotion, promotion_sku FROM cpgos_engine;
REVOKE SELECT ON deduction FROM cpgos_engine;
GRANT SELECT (deduction_id, claim_number, partner_id, remittance_line_id, deduction_code_id, raw_reason, amount,
              deduction_date, dispute_deadline, raw_invoice_ref, invoice_id, promotion_id, status,
              dataset_version_id, sku_id, claimed_units, period_start, period_end)
    ON deduction TO cpgos_engine;               -- every column except the true store
