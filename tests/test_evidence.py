"""Evidence damage 2.0: an overlay decided by business keys, and the repair of 1.0's changes.

Regressions for two mistakes in evidence-1.0.0:
- it drew random numbers in database-id order, so the same seed damaged different
  records on a machine whose ids differed (her Mac and the sandbox disagreed);
- it altered the system of record (deleted receipts and promotions, blanked dates).
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from cpgos.finance import deductions as ded
from cpgos.finance import evidence as EV
from cpgos.finance.world import build_world
from cpgos.ingest.reference import load_deduction_codes
from tests.conftest import ADMIN_URL, _url_for
from tests.test_finance_world import cfg, sales_fixture

WORLD = dict(p_promo_overclaim=0.0, p_promo_duplicate=1.0)
HEAVY = EV.EvidenceConfig(p_pos_week_missing=0.2, p_pos_week_restated=0.2, p_receipt_missing=0.3,
                          p_delivery_date_missing=0.3, p_promo_missing=0.3, p_promo_end_shifted=0.3,
                          p_claim_store_missing=0.3)


def _build(conn):
    load_deduction_codes(conn)
    build_world(conn, sales_fixture(), cfg(**WORLD))


@pytest.fixture
def world(conn):
    _build(conn)
    return conn


@pytest.fixture
def other_db(template_db):
    """A second database whose ids are all shifted, as on a machine that loaded other data first."""
    name = f"cpgos_t_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}" TEMPLATE "{template_db}"')
    with psycopg.connect(_url_for(name), autocommit=True) as c:
        for (seq,) in c.execute("SELECT schemaname || '.' || sequencename FROM pg_sequences").fetchall():
            c.execute("SELECT setval(%s, 7919)", (seq,))
        yield c
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_same_damage_on_every_machine_whatever_the_ids(world, other_db):
    _build(other_db)
    a_ids = world.execute("SELECT min(invoice_line_id) FROM invoice_line").fetchone()[0]
    b_ids = other_db.execute("SELECT min(invoice_line_id) FROM invoice_line").fetchone()[0]
    assert a_ids != b_ids                                                   # the ids really differ
    ra, rb = EV.degrade(world, HEAVY), EV.degrade(other_db, HEAVY)
    assert ra["changes"] > 10
    assert ra["world_fingerprint"] == rb["world_fingerprint"]
    assert ra["damage_fingerprint"] == rb["damage_fingerprint"]
    q = "SELECT kind, ref, detail::text FROM eval.gt_evidence WHERE version = %s ORDER BY 1, 2"
    assert world.execute(q, (EV.EVIDENCE_VERSION,)).fetchall() == other_db.execute(q, (EV.EVIDENCE_VERSION,)).fetchall()
    # and therefore the checker reaches the same verdicts, claim by claim
    va = {(a.claim_number, a.verdict, a.rule, a.recoverable) for a in ded.assess_all(world)}
    vb = {(a.claim_number, a.verdict, a.rule, a.recoverable) for a in ded.assess_all(other_db)}
    assert va == vb


def test_the_records_are_never_altered(world):
    before = EV.snapshot(world)
    rep = EV.degrade(world, HEAVY)
    assert rep["receipt_lines"]["missing"] > 0 and rep["shipments"]["undated"] > 0
    assert EV.snapshot(world) == before
    # ...while the checker's view does lose what was hidden
    seen = world.execute("SELECT count(*) FROM observed.receipt_line").fetchone()[0]
    real = world.execute("SELECT count(*) FROM receipt_line").fetchone()[0]
    assert real - seen == rep["receipt_lines"]["missing"]
    assert world.execute("SELECT count(*) FROM observed.deduction WHERE store_id IS NULL").fetchone()[0] \
        >= rep["claims"]["store_removed"]


def test_engine_reads_only_the_observed_records(world):
    EV.degrade(world, HEAVY)
    with world.transaction():
        world.execute("SET LOCAL ROLE cpgos_engine")
        assert world.execute("SELECT count(*) FROM observed.promotion").fetchone()[0] > 0
        world.execute("SELECT deduction_id, amount FROM deduction LIMIT 1")      # what it needs is still readable
    for sql in ("SELECT 1 FROM receipt_line", "SELECT 1 FROM promotion", "SELECT 1 FROM shipment",
                "SELECT 1 FROM retailer_pos_week", "SELECT store_id FROM deduction", "SELECT 1 FROM evidence.promo_end"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with world.transaction():
                world.execute("SET LOCAL ROLE cpgos_engine")
                world.execute(sql)


def test_overlay_is_applied_once_and_replace_is_explicit(world):
    first = EV.degrade(world, HEAVY)
    assert EV.degrade(world, HEAVY)["already_applied"] is True
    again = EV.degrade(world, HEAVY, replace=True)
    assert again["damage_fingerprint"] == first["damage_fingerprint"]
    n = world.execute("SELECT count(*) FROM eval.gt_evidence WHERE version = %s", (EV.EVIDENCE_VERSION,)).fetchone()[0]
    assert n == first["changes"]                                            # replaced, not doubled


def test_one_extra_record_changes_only_its_own_fate():
    # With a sequential random stream, one extra row shifts every draw after it. With hashing it cannot.
    keys = [f"KeHE|CA_{i}|FOODS_1_00{i % 9}|2015-03-0{i % 7 + 1}" for i in range(500)]
    base = {k for k in keys if EV.draw(1709, "receipt_missing", k) < 0.04}
    more = {k for k in ["extra|row"] + keys if EV.draw(1709, "receipt_missing", k) < 0.04}
    assert base == more - {"extra|row"}


# ---------- repair of 1.0's changes ----------

def _damage_like_v1(conn):
    """What evidence-1.0.0 did, directly to the records, with its log."""
    ver = conn.execute("SELECT max(dataset_version_id) FROM dataset_version WHERE dataset_key = 'finance_world'").fetchone()[0]
    log = []
    for lid, in conn.execute("SELECT invoice_line_id FROM receipt_line ORDER BY 1 LIMIT 3").fetchall():
        conn.execute("DELETE FROM receipt_line WHERE invoice_line_id = %s", (lid,))
        log.append(("receipt_missing", str(lid), "{}"))
    for sid, in conn.execute("SELECT shipment_id FROM shipment ORDER BY 1 LIMIT 2").fetchall():
        conn.execute("UPDATE shipment SET delivered_date = NULL WHERE shipment_id = %s", (sid,))
        log.append(("delivery_date_missing", str(sid), "{}"))
    st, sk, wk, u = conn.execute("SELECT store_id, sku_id, week_start, units FROM retailer_pos_week ORDER BY 1, 2, 3 LIMIT 1").fetchone()
    conn.execute("DELETE FROM retailer_pos_week WHERE store_id = %s AND sku_id = %s AND week_start = %s", (st, sk, wk))
    log.append(("pos_week_missing", "x", "{}"))
    conn.execute("UPDATE retailer_pos_week SET units = units + 2 WHERE (store_id, sku_id, week_start) = "
                 "(SELECT store_id, sku_id, week_start FROM retailer_pos_week WHERE units > 0 ORDER BY 1, 2, 3 DESC LIMIT 1)")
    log.append(("pos_week_restated", "x", "{}"))
    pids = [p for p, in conn.execute("""SELECT promotion_id FROM promotion p WHERE NOT EXISTS
                                        (SELECT 1 FROM deduction d WHERE d.promotion_id = p.promotion_id) ORDER BY 1""")]
    conn.execute("DELETE FROM promotion WHERE promotion_id = %s", (pids[0],))
    log.append(("promo_missing", str(pids[0]), "{}"))
    if len(pids) > 1:
        conn.execute("UPDATE promotion SET end_date = end_date + 7 WHERE promotion_id = %s", (pids[1],))
        log.append(("promo_end_shifted", str(pids[1]), "{}"))
    did = conn.execute("SELECT deduction_id FROM deduction WHERE store_id IS NOT NULL ORDER BY 1 LIMIT 1").fetchone()[0]
    conn.execute("UPDATE deduction SET store_id = NULL WHERE deduction_id = %s", (did,))
    log.append(("claim_store_missing", str(did), "{}"))
    for kind, ref, det in log:
        conn.execute("INSERT INTO eval.gt_evidence (dataset_version_id, kind, ref, detail) VALUES (%s, %s, %s, %s)",
                     (ver, kind, ref, det))
    conn.execute("INSERT INTO finance_evidence (dataset_version_id, version, report) VALUES (%s, %s, '{}')", (ver, EV.V1))
    return len(log)


def test_restore_puts_back_exactly_what_1_0_changed(world, db_url):
    truth = EV.snapshot(world)
    n = _damage_like_v1(world)
    assert EV.snapshot(world) != truth
    with pytest.raises(RuntimeError, match="restore"):
        EV.degrade(world)                                                    # refuses to build on altered records
    res = EV.restore_v1(world, db_url, _build, progress=lambda *_: None)
    assert EV.snapshot(world) == truth
    assert sum(res["repaired"].values()) == n and res["repair_equals_log"] is True
    assert EV.restore_v1(world, db_url, _build)["nothing_to_restore"] is True
    assert EV.degrade(world)["version"] == EV.EVIDENCE_VERSION               # now allowed
    leftovers = psycopg.connect(ADMIN_URL, autocommit=True).execute(
        "SELECT count(*) FROM pg_database WHERE datname LIKE 'cpgos_restore_%'").fetchone()[0]
    assert leftovers == 0                                                    # temporary database dropped


def test_restore_refuses_a_different_world(world, db_url):
    _damage_like_v1(world)
    before = EV.snapshot(world)

    def another_world(c):
        load_deduction_codes(c)
        build_world(c, sales_fixture(), cfg(**{**WORLD, "seed": 99}))
    with pytest.raises(EV.WorldMismatch):
        EV.restore_v1(world, db_url, another_world, progress=lambda *_: None)
    assert EV.snapshot(world) == before                                      # nothing written


def test_restore_also_fixes_claim_numbers_made_from_database_ids(other_db):
    # Generator 1.0 named claims after the remittance line's database id. A database whose ids did not
    # start at 1 (a failed first build advances sequences) got different claim numbers from every other machine.
    _build(other_db)
    truth = EV.snapshot(other_db)
    other_db.execute("UPDATE deduction SET claim_number = 'R-' || lpad(remittance_line_id::text, 7, '0')")
    assert EV.snapshot(other_db)["world"] != truth["world"]
    _damage_like_v1(other_db)
    url = _url_for(other_db.info.dbname)
    res = EV.restore_v1(other_db, url, _build, progress=lambda *_: None)
    assert res["repaired"]["claims_renumbered"] > 0
    assert EV.snapshot(other_db) == truth
