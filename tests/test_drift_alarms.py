"""Drift alarms hold the gate: raised by the monitor role, cleared only by a named person, audited."""

from __future__ import annotations

from datetime import date

import psycopg
import pytest

from cpgos.finance import cashapp, stp
from cpgos.monitor import alarms, drift
from tests.test_stp import _earn, world  # noqa: F401  (fixture)


def _fake(stream="cash", feature="reference_shape"):
    return drift.Alarm(stream, date(2016, 1, 1), drift.Check(feature, 60, 1e-9, 0.8, True,
                                                             [{"value": "other", "baseline": 0.01, "now": 0.4}]))


def _route(conn, line=1):
    return conn.execute("SELECT stp_route('exact_reference', %s, %s)", (cashapp.ENGINE_VERSION, line)).fetchone()[0]


def test_an_open_cash_alarm_sends_everything_to_a_person_until_cleared(world):
    _earn(world)
    world.execute("UPDATE stp_policy SET audit_rate = 0.05")
    before = [_route(world, i) for i in range(1, 200)]
    assert "auto" in before                                         # the lane has earned autonomy
    [aid] = alarms.raise_alarms(world, [_fake()])
    assert {_route(world, i) for i in range(1, 200)} == {"audit"}
    counts = cashapp.apply(world, cashapp.match_all(world, "full"))
    assert counts["auto"] == 0
    alarms.clear(world, aid, "akila", "payer changed remittance software; references checked by hand")
    assert [_route(world, i) for i in range(1, 200)] == before


def test_alarms_on_other_streams_do_not_hold_cash(world):
    _earn(world)
    world.execute("UPDATE stp_policy SET audit_rate = 0.05")
    alarms.raise_alarms(world, [_fake("prices", "shelf_price_drop")])
    assert "auto" in {_route(world, i) for i in range(1, 200)}


def test_one_open_alarm_per_feature_and_every_raise_is_audited(world):
    events = world.execute("SELECT count(*) FROM audit_event WHERE action = 'drift_alarm'").fetchone()[0]
    assert len(alarms.raise_alarms(world, [_fake()])) == 1
    assert alarms.raise_alarms(world, [_fake()]) == []
    assert len(alarms.open_alarms(world)) == 1
    assert world.execute("SELECT count(*) FROM audit_event WHERE action = 'drift_alarm'").fetchone()[0] == events + 1


def test_clearing_needs_a_name_and_a_reason_and_happens_once(world):
    [aid] = alarms.raise_alarms(world, [_fake()])
    for who, note in ((" ", "checked"), ("akila", " ")):
        with pytest.raises(alarms.AlarmError):
            alarms.clear(world, aid, who, note)
    alarms.clear(world, aid, " akila ", "checked")
    row = world.execute("SELECT cleared_by, clear_note FROM drift_alarm WHERE drift_alarm_id = %s", (aid,)).fetchone()
    assert row == ("akila", "checked")
    assert world.execute("SELECT actor_type, actor_id FROM audit_event WHERE action = 'drift_clear'").fetchall() == [("human", "akila")]
    with pytest.raises(alarms.AlarmError, match="already cleared"):
        alarms.clear(world, aid, "akila", "again")


@pytest.mark.parametrize("role,sql", [
    ("cpgos_engine", "INSERT INTO drift_alarm (stream, feature, checked_through, p_value, psi, moved, monitor_version) "
                     "VALUES ('cash', 'x', '2016-01-01', 0, 1, '[]', 'v')"),
    ("cpgos_engine", "UPDATE drift_alarm SET cleared_at = now(), cleared_by = 'engine', clear_note = 'x'"),
    ("cpgos_monitor", "UPDATE drift_alarm SET cleared_at = now(), cleared_by = 'monitor', clear_note = 'x'"),
    ("cpgos_monitor", "UPDATE stp_policy SET mode = 'off'"),
    ("cpgos_monitor", "INSERT INTO cash_application (remittance_line_id, invoice_id, applied_amount, method, match_explanation) "
                      "VALUES (1, 1, 1, 'exact', '{}')"),
])
def test_roles_cannot_step_outside_their_part(world, role, sql):
    alarms.raise_alarms(world, [_fake()])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with world.transaction():
            world.execute(f"SET LOCAL ROLE {role}")
            world.execute(sql)


def test_the_live_watch_raises_a_cash_alarm_when_the_payer_switches_to_po_numbers(world):
    # Regression for the gap this closes: before, nothing watched the inputs, so a payer changing its
    # reference format was invisible until spot checks happened to land on its lines.
    lines = [r[0] for r in world.execute(
        """SELECT r.remittance_line_id FROM remittance_line r JOIN payment p USING (payment_id)
           WHERE r.gross_amount IS NOT NULL ORDER BY p.payment_date, r.remittance_line_id""")]
    late = lines[int(len(lines) * 0.7):]
    world.execute("UPDATE remittance_line SET raw_invoice_ref = 'PO' || (1000000 + remittance_line_id)::text "
                  "WHERE remittance_line_id = ANY(%s)", (late,))
    small = drift.Policy(window=20, min_window=10, min_baseline=20)
    assert alarms.watch(world, small)
    opened = alarms.open_alarms(world)
    # This world's invoice numbers already look like "letters then digits", the same shape as a PO
    # number, so the shape alone cannot tell; the engine's outcome can (most lines stop matching).
    assert any(a["stream"] == "cash" for a in opened)
    assert any(a["feature"] == "engine_outcome" and a["moved"][0]["value"] == "unmatched" for a in opened)
    world.execute("UPDATE stp_policy SET mode = 'enforce'")
    assert world.execute("SELECT stp_route('exact_reference', %s, 1)", (cashapp.ENGINE_VERSION,)).fetchone()[0] == "audit"
    assert alarms.watch(world, small) == []                        # nothing stacks on a second run


def test_a_healthy_world_raises_nothing(world):
    assert alarms.watch(world) == []


def test_api_lists_and_clears_alarms(world, db_url, monkeypatch):
    from fastapi.testclient import TestClient
    from cpgos.api import app as api
    monkeypatch.setenv("CPGOS_DATABASE_URL", db_url)
    [aid] = alarms.raise_alarms(world, [_fake()])
    c = TestClient(api.app)
    body = c.get("/api/drift").json()
    assert body["holds_cash"] is True and [a["drift_alarm_id"] for a in body["open"]] == [aid]
    assert c.post(f"/api/drift/{aid}/clear", json={"reviewer": "akila", "note": " "}).status_code == 400
    assert c.post(f"/api/drift/{aid}/clear", json={"reviewer": "akila", "note": "checked"}).json()["cleared_by"] == "akila"
    assert c.get("/api/drift").json()["holds_cash"] is False
