-- Phase 3: the agent is judged on the same imperfect evidence as the engine, so it gets the same
-- view of the world. 0013 hid the undamaged originals from the engine role but not from the agent
-- role, which could read the true POS weeks, receipts, shipments, promotions, the true store of each
-- claim, and the damage report. An agent built on that role would score well by reading the answer
-- from under the overlay. It now reads only what a supplier would really have: observed.*.
REVOKE SELECT ON retailer_pos_week, receipt_line, shipment, promotion, promotion_sku, finance_evidence FROM cpgos_agent;
REVOKE SELECT ON deduction FROM cpgos_agent;
GRANT SELECT (deduction_id, claim_number, partner_id, remittance_line_id, deduction_code_id, raw_reason, amount,
              deduction_date, dispute_deadline, raw_invoice_ref, invoice_id, promotion_id, status,
              dataset_version_id, sku_id, claimed_units, period_start, period_end)
    ON deduction TO cpgos_agent;               -- every column except the true store
