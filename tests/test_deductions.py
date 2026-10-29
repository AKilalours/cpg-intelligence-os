from datetime import date, timedelta
from decimal import Decimal

import psycopg
import pytest

from cpgos.finance import deductions as ded
from cpgos.finance.world import build_world
from cpgos.ingest.reference import load_deduction_codes
from tests.test_finance_world import cfg, sales_fixture


# The fixture has one real promotion, which is either over-claimed or duplicated, so
# two worlds are needed to exercise every scan-back rule. Other bad claims are switched on.
@pytest.fixture(params=["overclaim", "duplicate"])
def world(conn, request):
    load_deduction_codes(conn)
    over = 1.0 if request.param == "overclaim" else 0.0
    return build_world(conn, sales_fixture(), cfg(p_promo_overclaim=over, p_promo_duplicate=1.0, p_promo_unsupported=1.0,
                                                  p_false_late_fee=0.3, p_false_shortage_invoice=0.3))


def _truth(conn):
    return {d: (v, Decimal(va), r) for d, v, va, r in
            conn.execute("SELECT deduction_id, is_valid, valid_amount, reason FROM eval.gt_deduction")}


def test_every_verdict_matches_the_hidden_truth(conn, world):
    truth = _truth(conn)
    got = ded.assess_all(conn)
    assert len(got) == len(truth) > 10
    reasons = {r for _, _, r in truth.values()}
    assert {"valid", "no_agreement", "delivered_on_time", "no_shortage_on_receipt"} <= reasons
    assert {"overclaim", "duplicate"} & reasons
    for a in got:
        is_valid, valid_amount, reason = truth[a.deduction_id]
        assert a.recoverable == a.amount - valid_amount, (a.claim_number, a.rule, reason, a.evidence)
        assert (a.verdict == "valid") == is_valid, (a.claim_number, a.rule, reason)


@pytest.fixture
def dup_world(conn):
    load_deduction_codes(conn)
    return build_world(conn, sales_fixture(), cfg(p_promo_overclaim=0.0, p_promo_duplicate=1.0))


def test_duplicate_is_found_by_arithmetic_not_by_name(conn, dup_world):
    got = {a.deduction_id: a for a in ded.assess_all(conn)}
    dups = [d for d, (_, _, r) in _truth(conn).items() if r == "duplicate"]
    assert dups
    for d in dups:
        a = got[d]
        assert a.rule == "scan_back_duplicate" and a.recoverable == a.amount
        assert any(Decimal(p["already_accepted"]) > 0 for p in a.evidence["promotions"])


def test_engine_role_cannot_read_the_answer_key(conn, world):
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_engine")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT 1 FROM eval.gt_deduction")


def test_engine_produces_the_same_verdicts_as_engine_role(conn, world):
    # The validator needs nothing the engine role cannot see.
    plain = [(a.deduction_id, a.recoverable) for a in ded.assess_all(conn)]
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_engine")
        as_engine = [(a.deduction_id, a.recoverable) for a in ded.assess_all(conn)]
    assert plain == as_engine


def test_save_writes_assessment_calculation_and_audit_and_is_append_only(conn, world):
    got = ded.assess_all(conn)
    assert ded.save(conn, got) == len(got)
    n, linked = conn.execute(
        "SELECT count(*), count(*) FILTER (WHERE calculation_id IS NOT NULL) FROM deduction_assessment").fetchone()
    assert n == linked == len(got)
    audit = conn.execute("SELECT count(*) FROM audit_event WHERE action = 'deduction_assessed'").fetchone()[0]
    assert audit == len(got)
    with pytest.raises(psycopg.errors.RaiseException, match="deduction_assessment is append-only"):
        conn.execute("UPDATE deduction_assessment SET recoverable_amount = 0")


def _a(i, amount, recoverable, deadline, ddate=date(2016, 1, 1)):
    return ded.Assessment(i, f"R-{i}", "WP", "", Decimal(amount), ddate, deadline, "invalid", "x", None, Decimal(recoverable))


def test_queue_puts_urgent_recoverable_claims_first_and_expired_last():
    today = date(2016, 5, 1)
    big_later = _a(1, "900", "900", today + timedelta(days=120))
    small_urgent = _a(2, "100", "100", today + timedelta(days=5))
    expired = _a(3, "5000", "5000", today - timedelta(days=1))
    valid = _a(4, "3000", "0", today + timedelta(days=60))
    tiny = _a(5, "10", "10", today + timedelta(days=2))    # below the cost of a dispute
    order = [a.deduction_id for a in ded.queue([big_later, small_urgent, expired, valid, tiny], today)]
    assert order[:2] == [2, 1]
    assert set(order[2:]) == {3, 4, 5}


def test_prioritization_recovers_at_least_as_much_as_simple_orders(conn, world):
    got = ded.assess_all(conn)
    truth = {d: Decimal(a) - Decimal(v) for d, a, v in conn.execute(
        "SELECT g.deduction_id, d.amount, g.valid_amount FROM eval.gt_deduction g JOIN deduction d USING (deduction_id)")}
    start = min(a.deduction_date for a in got)
    end = conn.execute("SELECT max(as_of_date) FROM world_clock").fetchone()[0]
    res = {p: ded.simulate_recovery(got, truth, p, 1, start, end) for p in ded.POLICIES}
    eng = Decimal(res["engine"]["recovered"])
    assert eng >= Decimal(res["largest_amount"]["recovered"])
    assert eng >= Decimal(res["oldest_first"]["recovered"])
    assert res["engine"]["false_disputes"] == 0


def test_evaluation_replays_after_reconcile_and_report_is_hidden_from_engine(conn, dup_world):
    from cpgos.finance import cashapp
    from cpgos.finance.evaluate import phase2a_report, save_report
    before = phase2a_report(conn)
    first = cashapp.match_all(conn, "full")
    cashapp.apply(conn, first)
    ded.save(conn, ded.assess_all(conn))
    # Only cash that was unmatched is retried (once other invoices are paid, an ambiguous
    # amount can become unique)...
    unmatched = {m.remittance_line_id for m in first if m.decision == "unmatched"}
    assert {m.remittance_line_id for m in cashapp.match_all(conn, "full")} <= unmatched
    after = phase2a_report(conn)                           # ...but evaluation replays from a clean slate
    assert after["cash_application"] == before["cash_application"]
    assert after["deductions"] == before["deductions"]
    save_report(conn, after, "test")
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_engine")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT 1 FROM eval.report")


def test_reconcile_twice_writes_nothing_new(conn, dup_world):
    # Regression: running reconcile a second time wrote a second, identical
    # assessment, calculation, and audit event for every deduction.
    got = ded.assess_all(conn)
    assert ded.save(conn, got) == len(got)
    counts = lambda: conn.execute(
        """SELECT (SELECT count(*) FROM deduction_assessment), (SELECT count(*) FROM calculation),
                  (SELECT count(*) FROM audit_event)""").fetchone()
    before = counts()
    assert ded.save(conn, ded.assess_all(conn)) == 0
    assert counts() == before
