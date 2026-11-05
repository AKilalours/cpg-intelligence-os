"""Fix 5: short-pays explained on the remittance line are posted; double-dips are named."""

from __future__ import annotations

from decimal import Decimal

from cpgos.finance import cashapp, deductions
from cpgos.finance.evaluate import score_cash_application
from cpgos.finance.world import build_world
from cpgos.ingest.reference import load_deduction_codes
from tests.test_finance_world import cfg, sales_fixture


def _world(conn, **kw):
    load_deduction_codes(conn)
    return build_world(conn, sales_fixture(), cfg(**kw))


def test_a_netted_line_is_consistent_and_its_claim_points_back_to_it(conn):
    _world(conn, p_deduct_at_payment=1.0, p_double_dip=0.0)
    rows = conn.execute("""SELECT r.remittance_line_id, r.gross_amount, r.deduction_amount, r.net_amount, g.amount, g.noise
                           FROM remittance_line r JOIN eval.gt_remittance_alloc g USING (remittance_line_id)
                           WHERE r.deduction_amount > 0""").fetchall()
    assert rows, "no invoice was paid net of a claim"
    for rl, gross, ded, net, truth_amount, noise in rows:
        assert gross - ded == net == truth_amount and noise == "deducted_at_payment"
        assert conn.execute("SELECT count(*) FROM deduction WHERE remittance_line_id = %s", (rl,)).fetchone()[0] == 1
    # every check still adds up: payment amount equals the sum of its lines' net amounts
    bad = conn.execute("""SELECT count(*) FROM payment p WHERE amount <> (SELECT sum(net_amount) FROM remittance_line r
                          WHERE r.payment_id = p.payment_id)""").fetchone()[0]
    assert bad == 0


def test_explained_short_pays_post_automatically_and_correctly(conn):
    _world(conn, p_deduct_at_payment=1.0, p_double_dip=0.0)
    matches = cashapp.match_all(conn, "full")
    s = score_cash_application(conn, matches)
    b = s["by_noise"]["deducted_at_payment"]
    assert b["n"] > 0 and b["auto_wrong"] == 0 and b["auto_correct"] == b["n"] - b["review"] - b["unmatched"]
    assert b["auto_correct"] > 0
    netted = [m for m in matches if m.deducted > 0 and m.decision == "auto"]
    assert all(m.amount == m.gross - m.deducted for m in netted)     # post what was paid, not the invoice total
    cashapp.apply(conn, matches)
    inv = conn.execute("""SELECT i.status FROM cash_application c JOIN invoice i USING (invoice_id)
                          WHERE c.remittance_line_id = %s""", (netted[0].remittance_line_id,)).fetchone()[0]
    assert inv == "partially_paid"                                    # the claim stays open until it is decided


def test_an_unexplained_short_pay_still_goes_to_a_person(conn):
    _world(conn, p_deduct_at_payment=0.0, p_double_dip=0.0, p_partial=1.0, p_typo_ref=0, p_missing_ref=0, p_wrong_ref=0)
    s = score_cash_application(conn, cashapp.match_all(conn, "full"))
    assert s["by_noise"]["partial"]["auto_correct"] + s["by_noise"]["partial"]["auto_wrong"] == 0


def test_a_double_dip_is_named_and_links_the_claim_it_repeats(conn):
    _world(conn, p_deduct_at_payment=1.0, p_double_dip=1.0)
    truth = dict(conn.execute("SELECT deduction_id, reason FROM eval.gt_deduction").fetchall())
    dips = {d for d, r in truth.items() if r == "duplicate"}
    assert dips
    out = {a.deduction_id: a for a in deductions.assess_all(conn)}
    claims = dict(conn.execute("SELECT deduction_id, claim_number FROM deduction").fetchall())
    for d in dips:
        a = out[d]
        if a.rule.startswith(("shortage", "late_fee")):
            assert a.verdict == "invalid" and a.rule.endswith("_duplicate"), (a.rule, a.verdict)
            first = a.evidence["earlier_claims"]
            assert first and all(c in claims.values() and c < claims[d] for c in first)
            assert a.recoverable == a.amount


def test_a_valid_second_claim_is_not_called_a_duplicate(conn):
    # Two shortages on different lines of one invoice are both valid: nothing is repeated.
    _world(conn, p_deduct_at_payment=0.0, p_double_dip=0.0, p_shortage_invoice=1.0)
    out = deductions.assess_all(conn)
    truth = dict(conn.execute("SELECT deduction_id, reason FROM eval.gt_deduction").fetchall())
    named = [a for a in out if a.rule.endswith("_duplicate")]
    assert all(truth[a.deduction_id] == "duplicate" for a in named)


def test_a_reference_quoting_the_wrong_invoice_with_a_twin_amount_is_not_auto_posted(conn):
    # Found 2026-11-05: two stores ordered the same goods, so two open invoices were both $603.60; the
    # remittance quoted the wrong one and the exact-reference, exact-amount rule posted it at 0.99.
    _world(conn, p_promo_unsupported=1.0, p_false_late_fee=0.3)
    truth = {rl: (inv, noise) for rl, inv, noise in
             conn.execute("SELECT remittance_line_id, invoice_id, noise FROM eval.gt_remittance_alloc")}
    matches = cashapp.match_all(conn, "full")
    wrong = [m for m in matches if m.decision == "auto" and m.invoice_id != truth[m.remittance_line_id][0]]
    assert wrong == []
    twins = [m for m in matches if "caution" in m.explanation]
    assert twins and all(m.decision == "review" and m.confidence < 0.90 for m in twins)
    assert any(truth[m.remittance_line_id][1] == "wrong_ref" for m in twins)


def test_the_report_counts_twins_and_named_duplicates(conn):
    from cpgos.finance.evaluate import score_deductions
    _world(conn, p_deduct_at_payment=1.0, p_double_dip=1.0, p_promo_unsupported=1.0, p_false_late_fee=0.3)
    matches = cashapp.match_all(conn, "full")
    s = score_cash_application(conn, matches)
    assert s["held_for_twin_amount"] == sum("caution" in m.explanation for m in matches)
    dup = score_deductions(conn, deductions.assess_all(conn))["by_reason"]["duplicate"]
    assert dup["n"] > 0 and dup["named_duplicate"] > 0 and dup["named_duplicate"] <= dup["verdict_correct"]
