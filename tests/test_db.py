import shutil

import psycopg
import pytest

from cpgos.db import MIGRATIONS_DIR, MigrationDriftError, migrate
from cpgos.ingest.reference import load_deduction_codes


def _seed_invoice(conn):
    brand = conn.execute("INSERT INTO brand (name) VALUES ('Fictional Fizz Co') RETURNING brand_id").fetchone()[0]
    partner = conn.execute(
        "INSERT INTO trading_partner (name, partner_type) VALUES ('KeHE', 'distributor') RETURNING partner_id"
    ).fetchone()[0]
    inv = conn.execute(
        """INSERT INTO invoice (invoice_number, brand_id, partner_id, invoice_date, total_amount)
           VALUES ('INV-100', %s, %s, '2026-03-01', 1200.00) RETURNING invoice_id""",
        (brand, partner),
    ).fetchone()[0]
    return brand, partner, inv


def test_migrations_are_idempotent(conn):
    assert migrate(conn) == []


def test_edited_migration_is_detected(conn, tmp_path):
    for f in MIGRATIONS_DIR.glob("*.sql"):
        shutil.copy(f, tmp_path / f.name)
    first = sorted(tmp_path.glob("*.sql"))[0]
    first.write_text(first.read_text() + "\n-- sneaky edit\n")
    with pytest.raises(MigrationDriftError):
        migrate(conn, tmp_path)


def test_audit_log_is_append_only(conn):
    eid = conn.execute(
        "INSERT INTO audit_event (actor_type, actor_id, action) VALUES ('human', 'reviewer-1', 'approve') RETURNING audit_event_id"
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE audit_event SET action='reject' WHERE audit_event_id=%s", (eid,))
    with pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM audit_event WHERE audit_event_id=%s", (eid,))


def test_agent_role_cannot_change_ledger(conn):
    _, _, inv = _seed_invoice(conn)
    conn.execute("SET ROLE cpgos_agent")
    try:
        # Reading is allowed...
        assert conn.execute("SELECT total_amount FROM invoice WHERE invoice_id=%s", (inv,)).fetchone()
        # ...writing the audit trail is allowed...
        conn.execute("INSERT INTO audit_event (actor_type, actor_id, action) VALUES ('agent', 'agent-v1', 'tool_call')")
        # ...but changing a ledger value is physically impossible.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE invoice SET total_amount = 0 WHERE invoice_id=%s", (inv,))
    finally:
        conn.execute("RESET ROLE")


def test_reviewer_can_change_status_but_not_amount(conn):
    _, partner, _ = _seed_invoice(conn)
    d = conn.execute(
        """INSERT INTO deduction (claim_number, partner_id, amount, deduction_date)
           VALUES ('C-1', %s, 514.80, '2026-03-10') RETURNING deduction_id""", (partner,)
    ).fetchone()[0]
    conn.execute("SET ROLE cpgos_reviewer")
    try:
        conn.execute("UPDATE deduction SET status='disputed' WHERE deduction_id=%s", (d,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE deduction SET amount=1 WHERE deduction_id=%s", (d,))
    finally:
        conn.execute("RESET ROLE")


def test_promo_flag_must_declare_its_source(conn):
    conn.execute("INSERT INTO trading_partner (name, partner_type) VALUES ('R', 'retailer')")
    conn.execute("INSERT INTO store (partner_id, external_code) SELECT partner_id, 'S1' FROM trading_partner")
    conn.execute("INSERT INTO product (sku_code) VALUES ('SKU1')")
    conn.execute(
        """INSERT INTO dataset_version (dataset_key, file_name, sha256, license, data_origin)
           VALUES ('x', 'f', repeat('a', 64), 'l', 'test_fixture')"""
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            """INSERT INTO sales_observation (obs_date, granularity, sku_id, store_id, units, promo_flag, dataset_version_id)
               SELECT '2026-01-01', 'day', p.sku_id, s.store_id, 1, true, d.dataset_version_id
               FROM product p, store s, dataset_version d"""
        )


def test_money_columns_are_exact(conn):
    _, _, inv = _seed_invoice(conn)
    conn.execute("UPDATE invoice SET total_amount = 0.1 + 0.2 WHERE invoice_id=%s", (inv,))
    assert str(conn.execute("SELECT total_amount FROM invoice WHERE invoice_id=%s", (inv,)).fetchone()[0]) == "0.30"


def test_kehe_codes_load_with_sources(conn):
    n = load_deduction_codes(conn)
    assert n >= 10
    assert load_deduction_codes(conn) == n  # idempotent
    rows = conn.execute("SELECT code, source_url FROM deduction_code").fetchall()
    assert len(rows) == n and all(url.startswith("https://") for _, url in rows)
    assert "XPP" in [c for c, _ in rows]
