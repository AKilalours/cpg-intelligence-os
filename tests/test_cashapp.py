from decimal import Decimal
import pytest

from cpgos.finance import cashapp
from cpgos.finance.evaluate import score_cash_application, wilson
from cpgos.finance.world import build_world
from cpgos.ingest.reference import load_deduction_codes
from tests.test_finance_world import cfg, sales_fixture


@pytest.mark.parametrize("a,b,expected", [
    ("NP1048213", "NP1048213", False),   # identical: not a near miss
    ("NP1048213", "NP1248203", False),   # two non-adjacent changes
    ("NP1048213", "NP1042813", True),    # 8 and 2 swapped: adjacent
    ("NP1048213", "NP1084213", True),    # adjacent swap
    ("NP1048213", "NP104823", True),     # one dropped digit
    ("NP1048213", "NP1048214", True),    # one substitution
    ("NP1048213", "NP10482133", True),   # one insertion
])
def test_within_one_edit(a, b, expected):
    assert cashapp.within_one_edit(a, b) is expected


def test_normalize_ref():
    assert cashapp.normalize_ref(" np-1048213 ") == "NP1048213"
    assert cashapp.normalize_ref("---") is None


def test_wilson_interval():
    assert wilson(0, 0) is None
    lo, hi = wilson(95, 100)
    assert lo < 0.95 < hi and 0.88 < lo < 0.90


@pytest.fixture
def world(conn):
    load_deduction_codes(conn)
    return build_world(conn, sales_fixture(), cfg())


def test_full_strategy_beats_exact_only_without_losing_precision(conn, world):
    full = score_cash_application(conn, cashapp.match_all(conn, "full"))
    base = score_cash_application(conn, cashapp.match_all(conn, "exact_only"))
    assert full["lines"] == base["lines"] > 50
    # Clean references are always applied automatically and correctly.
    clean = full["by_noise"]["clean"]
    assert clean["auto_correct"] == clean["n"] and clean["auto_wrong"] == 0
    # Typos are recovered by the full strategy and never by the baseline.
    assert full["by_noise"]["typo_ref"]["auto_correct"] > 0
    assert base["by_noise"]["typo_ref"]["auto_correct"] == 0
    # The baseline auto-applies every short-pay to the referenced invoice, so wrong references
    # to larger invoices become wrong postings. The full strategy sends those to review.
    assert full["false_match_rate"]["k"] == 0
    assert base["false_match_rate"]["k"] >= full["false_match_rate"]["k"]
    # More dollars posted correctly without a person, despite reviewing every short-pay.
    dollars = lambda r: Decimal(r["dollars_auto_applied_correctly"]["amount"])
    assert dollars(full) > dollars(base)


def test_no_wrong_automatic_matches(conn, world):
    # Regressions: (1) a reference to an already-paid invoice was "typo-corrected" to its
    # sequential neighbour; (2) a wrong reference to a larger invoice was auto-applied as a
    # partial payment. Both must now go to review, never to an automatic match.
    truth = dict(conn.execute("SELECT remittance_line_id, invoice_id FROM eval.gt_remittance_alloc"))
    wrong_auto = [m for m in cashapp.match_all(conn, "full")
                  if m.decision == "auto" and m.remittance_line_id in truth and m.invoice_id != truth[m.remittance_line_id]]
    assert wrong_auto == []


def test_reference_to_paid_invoice_is_never_fuzzy_matched(conn, world):
    matches = cashapp.match_all(conn, "full")
    fuzzy = [m for m in matches if m.explanation.get("reference", "").startswith("one edit")]
    refs = {r[0] for r in conn.execute("SELECT upper(invoice_number) FROM invoice")}
    assert all(cashapp.normalize_ref(m.raw_ref) not in refs for m in fuzzy)


def test_apply_runs_as_engine_and_marks_invoices(conn, world):
    counts = cashapp.apply(conn, cashapp.match_all(conn, "full"))
    assert counts["auto"] > 0
    approved = conn.execute("SELECT count(*) FROM cash_application WHERE status = 'approved'").fetchone()[0]
    assert approved == counts["auto"]
    assert conn.execute("SELECT count(*) FROM invoice WHERE status IN ('paid', 'partially_paid')").fetchone()[0] > 0
    # Re-running never re-decides a line that already has a decision.
    decided = {r[0] for r in conn.execute("SELECT remittance_line_id FROM cash_application")}
    assert all(m.remittance_line_id not in decided for m in cashapp.match_all(conn, "full"))
