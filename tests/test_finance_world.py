import uuid
from datetime import date, timedelta
from decimal import Decimal

import psycopg
import pytest

from cpgos.finance.world import WeeklySale, WorldConfig, build_world
from cpgos.ingest.reference import load_deduction_codes
from tests.conftest import ADMIN_URL, _url_for

START = date(2015, 1, 5)  # a Monday


def sales_fixture(weeks=40):
    """2 stores x 3 items; item B has a real-style 20% price drop in weeks 20-21 at store CA_1."""
    out = []
    for w in range(weeks):
        wk = START + timedelta(days=7 * w)
        for store, region in (("CA_1", "CA"), ("TX_1", "TX")):
            for item, base_units, price in (("FOODS_1_001", 50, "2.50"), ("FOODS_1_002", 120, "1.25"), ("FOODS_2_003", 30, "4.00")):
                p = Decimal(price)
                if item == "FOODS_1_002" and store == "CA_1" and w in (20, 21):
                    p = Decimal("1.00")
                # Varied units so invoice totals rarely repeat (real totals almost never do).
                jitter = (w * 37 + sum(map(ord, item + store))) % 23
                out.append(WeeklySale(store, region, item, "FOODS", item.rsplit("_", 1)[0], wk,
                                      base_units + jitter, p, 7))
    return out


def cfg(**kw):
    base = dict(weeks=40, n_skus=3, p_typo_ref=0.2, p_missing_ref=0.1, p_wrong_ref=0.1, p_partial=0.1,
                p_late=0.2, p_shortage_invoice=0.3, p_promo_overclaim=0.0)
    base.update(kw)
    return WorldConfig(**base)


@pytest.fixture
def world(conn):
    load_deduction_codes(conn)
    return build_world(conn, sales_fixture(), cfg())


def test_promotions_only_on_real_price_drops(conn, world):
    rows = conn.execute("""SELECT st.external_code, p.sku_code, pr.start_date, pr.end_date, pr.allowance_per_unit
                           FROM promotion pr JOIN promotion_sku USING (promotion_id) JOIN product p USING (sku_id)
                           JOIN store st ON st.store_id = pr.store_id""").fetchall()
    assert rows == [("CA_1", "FOODS_1_002", START + timedelta(days=140), START + timedelta(days=153), Decimal("0.1500"))]


def test_every_invoice_paid_at_most_once_and_checks_add_up(conn, world):
    assert conn.execute("""SELECT count(*) FROM (SELECT invoice_id FROM eval.gt_remittance_alloc
                           WHERE invoice_id IS NOT NULL GROUP BY 1 HAVING count(*) > 1) x""").fetchone()[0] == 0
    bad = conn.execute("""SELECT count(*) FROM payment p
                          WHERE p.amount <> (SELECT sum(net_amount) FROM remittance_line r WHERE r.payment_id = p.payment_id)""").fetchone()[0]
    assert bad == 0 and world.payments > 0


def test_every_deduction_has_truth_and_valid_amount_never_exceeds_claim(conn, world):
    n, with_truth, over = conn.execute("""SELECT count(*), count(g.deduction_id), count(*) FILTER (WHERE g.valid_amount > d.amount)
                                          FROM deduction d LEFT JOIN eval.gt_deduction g USING (deduction_id)""").fetchone()
    assert n == with_truth == world.deductions and over == 0
    reasons = {r[0] for r in conn.execute("SELECT DISTINCT reason FROM eval.gt_deduction")}
    assert reasons <= {"valid", "overclaim", "duplicate", "no_agreement", "no_shortage_on_receipt",
                       "delivered_on_time", "over_allowance"}


def test_valid_promo_deduction_equals_allowance_times_real_units(conn, world):
    jit = lambda w: (w * 37 + sum(map(ord, "FOODS_1_002" + "CA_1"))) % 23
    real_units = (120 + jit(20)) + (120 + jit(21))
    amt = conn.execute("""SELECT d.amount FROM deduction d JOIN eval.gt_deduction g USING (deduction_id)
                          WHERE d.raw_reason LIKE 'WALMART PROMO%%' AND g.reason = 'valid'""").fetchall()
    assert amt == [(Decimal("0.15") * real_units,)]


def test_shortage_deductions_match_receipts(conn, world):
    # A valid shortage claim always corresponds to a receipt short by exactly the claimed units.
    mismatched = conn.execute("""
        SELECT count(*) FROM deduction d JOIN eval.gt_deduction g USING (deduction_id)
        JOIN invoice i ON i.invoice_number = d.raw_invoice_ref
        JOIN invoice_line il ON il.invoice_id = i.invoice_id AND il.sku_id = d.sku_id
        JOIN receipt_line r USING (invoice_line_id)
        WHERE d.raw_reason = 'SHORTAGE' AND g.reason = 'valid' AND il.quantity - r.units_received <> d.claimed_units""").fetchone()[0]
    assert mismatched == 0


def test_engine_role_cannot_read_the_answer_key(conn, world):
    conn.execute("SET ROLE cpgos_engine")
    try:
        assert conn.execute("SELECT count(*) FROM deduction").fetchone()[0] > 0   # ledger: readable
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM eval.gt_deduction")
    finally:
        conn.execute("RESET ROLE")


def test_only_one_world_per_database(conn, world):
    with pytest.raises(RuntimeError, match="already exists"):
        build_world(conn, sales_fixture(), cfg())


def test_same_seed_same_world(template_db):
    def make():
        name = f"cpgos_w_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(ADMIN_URL, autocommit=True) as a:
            a.execute(f'CREATE DATABASE "{name}" TEMPLATE "{template_db}"')
        try:
            with psycopg.connect(_url_for(name), autocommit=True) as c:
                load_deduction_codes(c)
                build_world(c, sales_fixture(), cfg())
                return c.execute("""SELECT (SELECT sha256 FROM dataset_version WHERE dataset_key='finance_world'),
                                           (SELECT sum(amount) FROM payment), (SELECT sum(amount) FROM deduction),
                                           (SELECT string_agg(raw_invoice_ref, ',' ORDER BY remittance_line_id) FROM remittance_line)""").fetchone()
        finally:
            with psycopg.connect(ADMIN_URL, autocommit=True) as a:
                a.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    assert make() == make()


def test_every_invoice_due_before_as_of_gets_paid(conn):
    # Regression: payment-day jitter was re-drawn every week, so some invoices
    # could miss every payment window and silently never be paid.
    load_deduction_codes(conn)
    rep = build_world(conn, sales_fixture(), cfg(p_unpaid=0.0))
    unpaid = conn.execute("""SELECT count(*) FROM invoice i
                             WHERE i.invoice_date + 36 <= %s
                               AND NOT EXISTS (SELECT 1 FROM eval.gt_remittance_alloc g WHERE g.invoice_id = i.invoice_id)""",
                          (rep.as_of,)).fetchone()[0]
    assert unpaid == 0
