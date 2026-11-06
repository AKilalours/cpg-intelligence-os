"""People deciding on cash matches and deductions: reviewer role, audit, no double decisions, API."""

from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient

from cpgos.api import app as api
from cpgos.finance import cashapp, deductions
from cpgos.finance import review as fr
from cpgos.finance.world import build_world
from cpgos.ingest.reference import load_deduction_codes
from tests.test_finance_world import cfg, sales_fixture


@pytest.fixture
def world(conn):
    load_deduction_codes(conn)
    build_world(conn, sales_fixture(), cfg(p_promo_unsupported=1.0, p_false_late_fee=0.3))
    cashapp.apply(conn, cashapp.match_all(conn, "full"))
    deductions.save(conn, deductions.assess_all(conn))
    return conn


def _proposed(conn):
    return conn.execute("SELECT remittance_line_id FROM cash_application WHERE status = 'proposed' "
                        "ORDER BY remittance_line_id LIMIT 1").fetchone()


def _open_deduction(conn, verdict):
    return conn.execute("""SELECT d.deduction_id FROM deduction d JOIN deduction_assessment a USING (deduction_id)
                           WHERE d.status = 'open' AND a.verdict = %s ORDER BY d.deduction_id LIMIT 1""",
                        (verdict,)).fetchone()


def test_cash_approve_is_attributed_audited_and_final(world):
    row = _proposed(world)
    if row is None:
        pytest.skip("this world has no match waiting for a person")
    inv = world.execute("SELECT invoice_id FROM cash_application WHERE remittance_line_id = %s", row).fetchone()[0]
    r = fr.decide_cash(world, row[0], "approve", " akila ", note="remittance matches")
    assert r["status"] == "approved"
    assert world.execute("SELECT status FROM invoice WHERE invoice_id = %s", (inv,)).fetchone()[0] in ("paid", "partially_paid")
    a = world.execute("SELECT actor_type, actor_id, action, payload->>'note' FROM audit_event "
                      "WHERE actor_type = 'human'").fetchall()
    assert a == [("human", "akila", "cash_approve", "remittance matches")]
    with pytest.raises(fr.DecisionError, match="already approved"):
        fr.decide_cash(world, row[0], "reject", "akila")


def test_deduction_decisions_record_agreement_with_the_engine(world):
    inv, val = _open_deduction(world, "invalid"), _open_deduction(world, "valid")
    assert inv is not None
    assert fr.decide_deduction(world, inv[0], "dispute", "akila")["agrees_with_engine"] is True
    assert world.execute("SELECT status FROM deduction WHERE deduction_id = %s", inv).fetchone()[0] == "disputed"
    if val is not None:
        r = fr.decide_deduction(world, val[0], "dispute", "akila")
        assert r["agrees_with_engine"] is False                       # overruling the engine is visible
    with pytest.raises(fr.DecisionError, match="already disputed"):
        fr.decide_deduction(world, inv[0], "accept", "akila")


def test_bad_input_writes_nothing(world):
    events = world.execute("SELECT count(*) FROM audit_event").fetchone()[0]
    d = _open_deduction(world, "invalid")[0]
    for args in ((d, "delete", "akila"), (d, "dispute", "  "), (10**9, "dispute", "akila")):
        with pytest.raises(fr.DecisionError):
            fr.decide_deduction(world, *args)
    with pytest.raises(fr.DecisionError):
        fr.decide_cash(world, 10**9, "approve", "akila")
    assert world.execute("SELECT count(*) FROM audit_event").fetchone()[0] == events


def test_engine_role_cannot_take_human_decisions(world):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with world.transaction():
            world.execute("SET LOCAL ROLE cpgos_engine")
            world.execute("UPDATE deduction SET status = 'disputed'")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with world.transaction():
            world.execute("SET LOCAL ROLE cpgos_engine")
            world.execute("UPDATE cash_application SET status = 'approved'")


@pytest.fixture
def client(db_url, world, monkeypatch):
    monkeypatch.setenv("CPGOS_DATABASE_URL", db_url)
    return TestClient(api.app)


def test_api_round_trip(client, world):
    d = _open_deduction(world, "invalid")[0]
    r = client.post(f"/api/deductions/{d}/review", json={"action": "dispute", "reviewer": "akila"})
    assert r.status_code == 200 and r.json()["status"] == "disputed"
    again = client.post(f"/api/deductions/{d}/review", json={"action": "dispute", "reviewer": "akila"})
    assert again.status_code == 400 and "already" in again.json()["detail"]
    assert client.post("/api/cash/999999999/review", json={"action": "approve", "reviewer": "akila"}).status_code == 400
    q = client.get("/api/finance/cash-application").json()["review_queue"]
    assert all("invoice_amount" in x for x in q)


def test_api_counts_people_apart_from_the_engine(client, world):
    before = client.get("/api/finance/cash-application").json()
    row = _proposed(world)
    if row is None:
        pytest.skip("this world has no match waiting for a person")
    assert client.post(f"/api/cash/{row[0]}/review", json={"action": "approve", "reviewer": "akila"}).status_code == 200
    after = client.get("/api/finance/cash-application").json()
    assert after["auto_applied"] == before["auto_applied"]                   # a person's approval is not automatic
    assert after["approved_by_person"] == 1 and after["in_review"] == before["in_review"] - 1


def test_money_flow_streams_are_disjoint_and_add_up(client, world):
    from decimal import Decimal
    f = client.get("/api/overview/flow").json()
    from cpgos.finance.cashapp import CASH_LINE_SQL
    cash_total = world.execute(f"""SELECT coalesce(sum(net_amount), 0), count(*) FROM remittance_line r
                                   WHERE {CASH_LINE_SQL}""").fetchone()
    assert sum(Decimal(x["amount"]) for x in f["cash"]) == cash_total[0]
    assert sum(x["count"] for x in f["cash"]) == cash_total[1]
    ded_total = world.execute("SELECT sum(amount), count(*) FROM deduction").fetchone()
    assert sum(Decimal(x["amount"]) for x in f["deductions"]) == ded_total[0]
    assert sum(x["count"] for x in f["deductions"]) == ded_total[1]
    d = _open_deduction(world, "invalid")[0]
    client.post(f"/api/deductions/{d}/review", json={"action": "dispute", "reviewer": "akila"})
    after = {x["key"]: x["count"] for x in client.get("/api/overview/flow").json()["deductions"]}
    assert after["decided"] == 1                                          # a person's decision moves the money
