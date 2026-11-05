"""The agent sees what a supplier would really have, never the truth under the damaged evidence."""

from __future__ import annotations

import psycopg
import pytest

HIDDEN = ["SELECT 1 FROM retailer_pos_week", "SELECT 1 FROM receipt_line", "SELECT 1 FROM shipment",
          "SELECT 1 FROM promotion", "SELECT 1 FROM promotion_sku", "SELECT 1 FROM finance_evidence",
          "SELECT store_id FROM deduction", "SELECT 1 FROM eval.gt_deduction", "SELECT 1 FROM eval.gt_remittance_alloc"]
VISIBLE = ["SELECT 1 FROM observed.retailer_pos_week", "SELECT 1 FROM observed.receipt_line",
           "SELECT 1 FROM observed.shipment", "SELECT 1 FROM observed.promotion", "SELECT 1 FROM observed.deduction",
           "SELECT claim_number, amount FROM deduction", "SELECT 1 FROM invoice", "SELECT 1 FROM trade_term"]


@pytest.mark.parametrize("sql", HIDDEN)
def test_the_agent_cannot_read_the_truth(conn, sql):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.transaction():
            conn.execute("SET LOCAL ROLE cpgos_agent")
            conn.execute(sql)


@pytest.mark.parametrize("sql", VISIBLE)
def test_the_agent_can_read_what_a_supplier_has(conn, sql):
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_agent")
        conn.execute(sql)


def test_the_agent_cannot_change_the_ledger(conn):
    for sql in ("UPDATE deduction SET status = 'disputed'", "UPDATE invoice SET status = 'paid'",
                "INSERT INTO cash_application (remittance_line_id, invoice_id, applied_amount, method, confidence, "
                "match_explanation, status) VALUES (1, 1, 1, 'x', 1, '{}', 'approved')"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("SET LOCAL ROLE cpgos_agent")
                conn.execute(sql)
