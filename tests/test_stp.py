"""Addition 11: the error budget. Autonomy per lane is earned from people's reviews, kept honest by
an audit sample, lost on evidence of mistakes, and enforced by the database, not by the engine."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import psycopg
import pytest

from cpgos.finance import cashapp, stp
from cpgos.finance import review as fr
from cpgos.finance.world import build_world
from cpgos.ingest.reference import load_deduction_codes
from tests.test_finance_world import cfg, sales_fixture


@pytest.fixture
def world(conn):
    load_deduction_codes(conn)
    build_world(conn, sales_fixture(), cfg())
    return conn


def _enforce(conn, max_error_rate=0.40, audit_rate=0.05):
    conn.execute("UPDATE stp_policy SET mode = 'enforce', max_error_rate = %s, audit_rate = %s",
                 (max_error_rate, audit_rate))


def _audits(conn, lane="exact_reference"):
    return [r[0] for r in conn.execute(
        """SELECT remittance_line_id FROM cash_application WHERE routed = 'audit' AND status = 'proposed'
           AND lane = %s ORDER BY remittance_line_id""", (lane,))]


# --------------------------------------------------------------- arithmetic

@pytest.mark.parametrize("k,n", [(0, 1), (0, 299), (1, 10), (2, 50), (0, 1000), (5, 1000)])
def test_python_and_sql_bounds_agree(conn, k, n):
    sql = conn.execute("SELECT stp_upper_bound(%s, %s, 0.95)", (k, n)).fetchone()[0]
    assert abs(sql - stp.upper_bound(k, n)) < 1e-9


def test_rule_of_three():
    assert stp.reviews_needed(0.01) == 299                    # 0 errors in 299 reviews: below 1% at 95%
    assert stp.upper_bound(0, 0) == 1.0 and stp.upper_bound(3, 3) == 1.0
    assert stp.upper_bound(1, 10) > stp.upper_bound(0, 10)   # an error always raises the bound


# --------------------------------------------------------------- the database decides

def test_closed_lane_sends_every_would_be_posting_to_a_person(world):
    # Regression for the old contract: confidence >= 0.90 posted cash with no evidence that the
    # engine's posting lanes were ever checked. Under enforcement, a lane with no reviews is closed.
    _enforce(world)
    counts = cashapp.apply(world, cashapp.match_all(world, "full"))
    assert counts["auto"] == 0 and counts["audit"] > 0
    assert world.execute("SELECT count(*) FROM cash_application WHERE status = 'approved'").fetchone()[0] == 0
    assert world.execute("SELECT count(*) FROM cash_application WHERE routed = 'audit'").fetchone()[0] == counts["audit"]


def test_off_mode_keeps_todays_behaviour(world):
    counts = cashapp.apply(world, cashapp.match_all(world, "full"))
    assert counts["auto"] > 0 and counts["audit"] == 0
    assert world.execute("SELECT count(*) FROM cash_application WHERE routed = 'auto'").fetchone()[0] == counts["auto"]


def test_engine_cannot_post_around_the_gate(world):
    _enforce(world)
    m = next(m for m in cashapp.match_all(world, "full") if m.decision == "auto")
    ins = """INSERT INTO cash_application (remittance_line_id, invoice_id, applied_amount, method, confidence,
                                           match_explanation, status, lane, engine_version)
             VALUES (%s, %s, %s, 'exact', 0.99, '{}', 'approved', %s, %s)"""
    for lane in ("exact_reference", None):
        with pytest.raises(psycopg.errors.RaiseException, match="error budget"):
            with world.transaction():
                world.execute("SET LOCAL ROLE cpgos_engine")
                world.execute(ins, (m.remittance_line_id, m.invoice_id, m.amount, lane, cashapp.ENGINE_VERSION))


def test_engine_cannot_change_the_policy_or_see_the_sample(world):
    for sql in ("UPDATE stp_policy SET mode = 'off'", "SELECT salt FROM stp_policy",
                "UPDATE cash_application SET routed = 'auto'"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with world.transaction():
                world.execute("SET LOCAL ROLE cpgos_engine")
                world.execute(sql)
    with world.transaction():                                   # what it may see: the policy itself
        world.execute("SET LOCAL ROLE cpgos_engine")
        assert world.execute("SELECT count(*) FROM stp_policy").fetchone()[0] == 3


def test_policy_changes_are_attributed(world):
    _enforce(world)
    who = world.execute("SELECT session_user").fetchone()[0]
    rows = world.execute("SELECT actor_type, actor_id, payload->'after'->>'mode', payload->'after'->>'audit_rate', "
                         "payload->'after' ? 'salt' FROM audit_event WHERE action = 'stp_policy_change' "
                         "ORDER BY audit_event_id").fetchall()
    # Migration 0022 set the chosen audit rate (decision 95): recorded, as a system act naming it.
    assert rows[:3] == [("system", "migration 0022_audit_rate_chosen", "off", "0.200", False)] * 3
    assert rows[3:] == [("human", who, "enforce", "0.050", False)] * 3
    assert world.execute("SELECT DISTINCT updated_by FROM stp_policy").fetchall() == [(who,)]


# --------------------------------------------------------------- earning and losing autonomy

def _earn(world, lane="exact_reference"):
    """First half of the lines goes to people (lane closed); people approve the audits that are right."""
    _enforce(world)
    matches = sorted(cashapp.match_all(world, "full"), key=lambda m: (m.payment_date, m.remittance_line_id))
    cashapp.apply(world, matches[: len(matches) // 2])
    truth = dict(world.execute("SELECT remittance_line_id, invoice_id FROM eval.gt_remittance_alloc"))
    ids = _audits(world, lane)
    assert len(ids) >= stp.reviews_needed(0.40), "fixture world too small for this test"
    for rl in ids:
        inv = world.execute("SELECT invoice_id FROM cash_application WHERE remittance_line_id = %s", (rl,)).fetchone()[0]
        fr.decide_cash(world, rl, "approve" if truth.get(rl) == inv else "reject", "akila")
    return truth


def test_reviews_open_a_lane_and_it_starts_posting(world):
    _earn(world)
    st = stp.lane_status(world, "exact_reference", cashapp.ENGINE_VERSION)
    assert st["is_open"] and st["errors"] == 0 and st["upper_bound"] <= 0.40
    counts = cashapp.apply(world, cashapp.match_all(world, "full"))
    assert counts["auto"] > 0
    posted = world.execute("SELECT count(*) FROM cash_application WHERE routed = 'auto' AND status = 'approved' "
                           "AND lane = 'exact_reference'").fetchone()[0]
    assert posted == world.execute("SELECT count(*) FROM cash_application WHERE status = 'approved' "
                                   "AND routed = 'auto'").fetchone()[0] > 0


def test_one_rejected_review_can_close_a_lane(world):
    _earn(world)
    # Audit every remaining line: the sample's salt is random per database, so a partial rate on this
    # small world could draw no audit at all and the test would depend on luck (it did, once in three).
    world.execute("UPDATE stp_policy SET audit_rate = 1.0")
    assert stp.lane_status(world, "exact_reference", cashapp.ENGINE_VERSION)["is_open"]
    cashapp.apply(world, cashapp.match_all(world, "full"))
    audit = _audits(world)
    assert audit, "an open lane still sends a sample to people"
    fr.decide_cash(world, audit[0], "reject", "akila", note="spot check: wrong invoice")
    st = stp.lane_status(world, "exact_reference", cashapp.ENGINE_VERSION)
    assert st["errors"] == 1 and not st["is_open"]


def test_a_reversed_automatic_posting_stops_the_lane_and_reopens_the_invoice(world):
    _earn(world)
    cashapp.apply(world, cashapp.match_all(world, "full"))
    rl, inv = world.execute("SELECT remittance_line_id, invoice_id FROM cash_application WHERE routed = 'auto' "
                            "AND status = 'approved' ORDER BY 1 LIMIT 1").fetchone()
    with pytest.raises(fr.DecisionError, match="say why"):
        fr.reverse_cash(world, rl, "akila", note=" ")
    r = fr.reverse_cash(world, rl, "akila", note="customer says this paid a different invoice")
    assert r["stops_lane"] == "exact_reference"
    assert world.execute("SELECT status FROM invoice WHERE invoice_id = %s", (inv,)).fetchone()[0] == "open"
    st = stp.lane_status(world, "exact_reference", cashapp.ENGINE_VERSION)
    assert st["reversals"] == 1 and not st["is_open"]
    assert world.execute("SELECT stp_route('exact_reference', %s, 1)", (cashapp.ENGINE_VERSION,)).fetchone()[0] == "audit"
    with pytest.raises(fr.DecisionError, match="only a posted match"):
        fr.reverse_cash(world, rl, "akila", note="again")
    # Reopening is a deliberate, audited act by whoever owns the policy.
    world.execute("UPDATE stp_policy SET max_error_rate = max_error_rate WHERE lane = 'exact_reference'")
    assert stp.lane_status(world, "exact_reference", cashapp.ENGINE_VERSION)["is_open"]


def test_a_new_engine_version_starts_with_no_autonomy(world):
    _earn(world)
    assert stp.lane_status(world, "exact_reference", cashapp.ENGINE_VERSION)["is_open"]
    st = stp.lane_status(world, "exact_reference", "cashapp-9.9.9")
    assert st["reviews"] == 0 and not st["is_open"]


def test_the_audit_sample_is_stable_and_near_its_rate(world):
    world.execute("UPDATE stp_policy SET mode = 'enforce', max_error_rate = 0.99, audit_rate = 0.10")
    # Open the lane with one approved review so routing samples instead of auditing everything.
    m = next(m for m in cashapp.match_all(world, "full") if m.decision == "auto" and m.confidence == 0.99)
    cashapp.apply(world, [m])
    fr.decide_cash(world, m.remittance_line_id, "approve", "akila")
    routes = [world.execute("SELECT stp_route('exact_reference', %s, %s)", (cashapp.ENGINE_VERSION, i)).fetchone()[0]
              for i in range(1, 2001)]
    again = [world.execute("SELECT stp_route('exact_reference', %s, %s)", (cashapp.ENGINE_VERSION, i)).fetchone()[0]
             for i in range(1, 2001)]
    assert routes == again
    assert 0.075 < routes.count("audit") / 2000 < 0.125      # about 3.7 standard deviations


# --------------------------------------------------------------- the simulator

def _history(n=900, lane="exact_reference", start=date(2024, 1, 1)):
    return [stp.Item(i, start + timedelta(days=i // 3), lane, True, Decimal(100)) for i in range(n)]


def test_simulation_healthy_history_opens_and_posts_nothing_wrong():
    r = stp.simulate(_history())
    assert r["wrong_posted"] == 0 and r["posted_by_engine"] > 0
    assert "exact_reference@v" in r["opened"]
    assert r["sent_to_a_person"] >= stp.reviews_needed(0.01)


def test_simulation_a_silent_fault_is_contained():
    items = _history(3000)
    fault = stp.inject_fault(items, "exact_reference", date(2024, 1, 1) + timedelta(days=600), 0.20)
    gated, ungated = stp.simulate(fault), stp.no_gate(fault)
    assert ungated["wrong_posted"] > 100
    assert gated["closed_after_opening"], "the lane must close once audits find the fault"
    assert gated["wrong_posted"] < ungated["wrong_posted"] / 5
    assert gated["wrong_caught_in_review"] > 0


def test_simulation_a_bad_release_posts_nothing_until_it_earns_it():
    items = _history(1500)
    bad = stp.inject_fault(items, "exact_reference", date(2024, 1, 1) + timedelta(days=200), 0.10,
                           version="v2")
    r = stp.simulate(bad)
    new = [i for i in bad if i.version == "v2"]
    assert new and r["wrong_posted"] == 0
    assert "exact_reference@v2" not in r["opened"]


def test_scenarios_run_on_a_real_world_without_touching_the_ledger(world):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import stp as script
    before = world.execute("SELECT count(*) FROM cash_application").fetchone()[0]
    items = script._items(world)
    assert items and all(i.lane in stp.LANES.values() for i in items)
    out = script.scenarios(items)
    assert world.execute("SELECT count(*) FROM cash_application").fetchone()[0] == before
    assert out["healthy_no_gate"]["wrong_posted"] == 0              # engine 1.1 posts nothing wrong here
    for r in out["silent_fault"].values():
        assert r["gate"]["wrong_posted"] <= r["no_gate"]["wrong_posted"]
    assert out["bad_release"]["gate"]["wrong_posted"] == 0


def test_api_shows_what_the_gate_will_decide(world, db_url, monkeypatch):
    from fastapi.testclient import TestClient
    from cpgos.api import app as api
    monkeypatch.setenv("CPGOS_DATABASE_URL", db_url)
    _earn(world)
    body = TestClient(api.app).get("/api/stp").json()
    lanes = {l["lane"]: l for l in body["lanes"]}
    assert set(lanes) == set(stp.LANES.values()) and body["engine_version"] == cashapp.ENGINE_VERSION
    ex = lanes["exact_reference"]
    assert ex["mode"] == "enforce" and ex["is_open"] is True and ex["errors"] == 0
    assert ex["reviews_needed"] == stp.reviews_needed(0.40) and isinstance(ex["upper_bound"], float)


def _reference_simulate(items, budget, salt):
    """The first, plainly written simulator (stp-sim-1.0.0): rescans every review each day."""
    lanes, state, posted = {}, {}, []
    for day in sorted({i.day for i in items}):
        for key, ev in lanes.items():
            seen = [c for d, c in ev if d <= day][-budget.window:]
            n, k = len(seen), sum(1 for c in seen if not c)
            state[key] = n > 0 and stp.upper_bound(k, n, budget.confidence) <= budget.max_error_rate
        for it in sorted((i for i in items if i.day == day), key=lambda i: i.line):
            key = (it.lane, it.version)
            ev = lanes.setdefault(key, [])
            if state.get(key, False) and stp._u(salt, it.lane, it.line) >= budget.audit_rate:
                posted.append(it.line)
            else:
                ev.append((it.day + timedelta(days=budget.review_delay_days), it.correct))
    return posted


@pytest.mark.parametrize("seed", range(6))
def test_fast_simulator_matches_the_plain_one(seed):
    import random
    rng = random.Random(seed)
    items = [stp.Item(i, date(2024, 1, 1) + timedelta(days=i // rng.choice([1, 2, 4])),
                      rng.choice(list(stp.LANES.values())), rng.random() > 0.004, Decimal(10)) for i in range(900)]
    items = stp.inject_fault(items, "exact_reference", date(2024, 6, 1), 0.2, seed=seed,
                             version="v2" if seed % 2 else None)
    b = stp.Budget(max_error_rate=0.05, audit_rate=0.1, window=150)
    r = stp.simulate(items, b, salt=f"s{seed}")
    posted_fast = r["posted_by_engine"]
    ref = _reference_simulate(items, b, f"s{seed}")
    assert posted_fast == len(ref)
    assert r["wrong_posted"] == sum(1 for i in items if i.line in set(ref) and not i.correct)


def test_monte_carlo_and_the_rule_for_choosing_the_audit_rate():
    items = _history(1200)
    mc = stp.monte_carlo([items], stp.Budget(audit_rate=0.2), runs=20, fault_rate=0.2)
    assert mc["runs"] == 20 and mc["wrong_posted_no_gate"]["median"] > mc["wrong_posted_with_gate"]["p90"]
    assert mc["lines_to_close"]["median"] > 0
    fake = {str(r): {"fault_5pct": {"wrong_posted_with_gate": {"p90": p90}, "wrong_posted_no_gate": {"median": 10}}}
            for r, p90 in zip(stp.AUDIT_RATES, (9, 6, 5, 1))}
    assert stp.choose_audit_rate(fake) == 0.10                     # first rate at or under half of 10
    worst = {k: {"fault_5pct": {"wrong_posted_with_gate": {"p90": 9}, "wrong_posted_no_gate": {"median": 10}}} for k in fake}
    assert stp.choose_audit_rate(worst) == stp.AUDIT_RATES[-1]


def test_frontier_command_records_the_rule_and_the_choice(world, monkeypatch, capsys):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import stp as script
    monkeypatch.setattr(script, "RUNS", 3)
    assert script.cmd_frontier(world) == 0
    body = world.execute("SELECT body FROM eval.report WHERE kind = 'stp_frontier'").fetchone()[0]
    assert body["chosen_audit_rate"] in stp.AUDIT_RATES and body["rule"].startswith("the smallest audit rate")
    assert set(body["frontier"]) == {str(r) for r in stp.AUDIT_RATES}
    assert "chosen audit rate" in capsys.readouterr().out


def test_replay_puts_the_gate_against_no_gate_week_by_week():
    items = _history(3000)
    r = stp.replay(items, fault_rate=0.2, audit_rate=0.2, seed=3)
    assert len(r["days"]) == len(r["gate"]) == len(r["no_gate"])
    assert r["no_gate"][-1] > r["gate"][-1] and r["opened"] and r["closed"]
    assert all(b >= a for a, b in zip(r["gate"], r["gate"][1:]))          # cumulative
    clean = stp.replay(items, fault_rate=0.0, audit_rate=0.2)
    assert clean["no_gate"][-1] == clean["gate"][-1] == 0
