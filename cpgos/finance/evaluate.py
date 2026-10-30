"""Score engine decisions against the hidden answer key (eval.*).

Runs with a privileged connection AFTER the engine has decided; the engine
itself never has access to eval.*. Every rate is reported with its count and
a 95% Wilson interval, because small categories (e.g. 27 wrong references)
make bare percentages misleading.
"""

from __future__ import annotations

import math
from collections import defaultdict
from decimal import Decimal

import psycopg

from cpgos.finance import cashapp, deductions
from cpgos.finance.cashapp import Match


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson score interval for k successes out of n."""
    if n == 0:
        return None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def rate(k: int, n: int) -> dict:
    return {"k": k, "n": n, "rate": round(k / n, 4) if n else None, "ci95": wilson(k, n)}


def score_cash_application(conn: psycopg.Connection, matches: list[Match]) -> dict:
    truth = {rl: (inv, Decimal(amt), noise) for rl, inv, amt, noise in
             conn.execute("SELECT remittance_line_id, invoice_id, amount, noise FROM eval.gt_remittance_alloc")}
    scored = [(m, truth[m.remittance_line_id]) for m in matches if m.remittance_line_id in truth]
    total_dollars = sum((t[1] for _, t in scored), Decimal(0))

    def correct(m: Match, t) -> bool:
        return m.invoice_id is not None and m.invoice_id == t[0]

    auto = [(m, t) for m, t in scored if m.decision == "auto"]
    review = [(m, t) for m, t in scored if m.decision == "review"]
    unmatched = [(m, t) for m, t in scored if m.decision == "unmatched"]
    auto_ok = sum(correct(m, t) for m, t in auto)
    out = {
        "lines": len(scored),
        "auto_applied": rate(len(auto), len(scored)),
        "auto_precision": rate(auto_ok, len(auto)),
        "false_match_rate": rate(len(auto) - auto_ok, len(auto)),
        "sent_to_review": rate(len(review), len(scored)),
        "review_suggestion_correct": rate(sum(correct(m, t) for m, t in review), len(review)),
        "unmatched": rate(len(unmatched), len(scored)),
        "dollars_auto_applied_correctly": {
            "amount": str(sum((m.amount for m, t in auto if correct(m, t)), Decimal(0))),
            "of_total": str(total_dollars),
            "share": round(float(sum((m.amount for m, t in auto if correct(m, t)), Decimal(0)) / total_dollars), 4) if total_dollars else None,
        },
    }
    by_noise = defaultdict(lambda: {"n": 0, "auto_correct": 0, "auto_wrong": 0, "review": 0, "unmatched": 0})
    for m, t in scored:
        b = by_noise[t[2]]
        b["n"] += 1
        if m.decision == "auto":
            b["auto_correct" if correct(m, t) else "auto_wrong"] += 1
        else:
            b[m.decision] += 1
    out["by_noise"] = dict(sorted(by_noise.items()))
    curve = []
    for th in (0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 0.99):
        sel = [(m, t) for m, t in scored if m.invoice_id is not None and m.confidence >= th]
        ok = sum(correct(m, t) for m, t in sel)
        curve.append({"threshold": th, "coverage": round(len(sel) / len(scored), 4) if scored else None,
                      "precision": round(ok / len(sel), 4) if sel else None, "wrong": len(sel) - ok})
    out["precision_coverage_curve"] = curve
    return out


DESIGN_REGIONS = ("CA",)              # deduction checker 2.0 was tuned on California stores only
REPORT_REGIONS = ("TX", "WI")         # and is reported on Texas and Wisconsin


def _deduction_block(scored) -> dict:
    """scored: [(assessment, (is_valid, true_recoverable, reason))]. A claim sent for more evidence is
    neither approved nor disputed; it is counted on its own, not as a mistake or a success."""
    decided = [(a, t) for a, t in scored if a.verdict != "needs_evidence"]
    invalid = [(a, t) for a, t in decided if not t[0]]
    valid = [(a, t) for a, t in decided if t[0]]
    return {
        "deductions": len(scored),
        "needs_evidence": rate(len(scored) - len(decided), len(scored)),
        "recoverable_exact_to_the_cent_when_decided": rate(sum(a.recoverable == t[1] for a, t in decided), len(decided)),
        "false_approval": rate(sum(a.verdict == "valid" for a, _ in invalid), len(invalid)),
        "false_dispute": rate(sum(a.verdict in ("invalid", "partially_valid") for a, _ in valid), len(valid)),
        "true_recoverable_total": str(sum((t[1] for _, t in scored), Decimal(0))),
        "found_recoverable_total": str(sum((a.recoverable for a, _ in scored), Decimal(0))),
    }


def score_deductions(conn: psycopg.Connection, assessments: list[deductions.Assessment]) -> dict:
    """Verdicts and recoverable amounts against eval.gt_deduction, overall, for the design region,
    and for the report region; plus which kind of evidence damage touched each mistake."""
    truth = {d: (v, Decimal(a) - Decimal(va), r) for d, v, va, r, a in conn.execute(
        """SELECT g.deduction_id, g.is_valid, g.valid_amount, g.reason, d.amount
           FROM eval.gt_deduction g JOIN deduction d USING (deduction_id)""")}
    store = dict(conn.execute("SELECT deduction_id, store_id FROM deduction"))
    for ref, det in conn.execute("SELECT ref, detail FROM eval.gt_evidence WHERE kind = 'claim_store_missing'"):
        store[int(ref)] = det["store_id"]             # the true store of a claim that arrived without one
    region = {sid: code[:2] for sid, code in conn.execute("SELECT store_id, external_code FROM store")}
    scored = [(a, truth[a.deduction_id]) for a in assessments if a.deduction_id in truth]
    by_reason = defaultdict(lambda: {"n": 0, "verdict_correct": 0, "needs_evidence": 0, "true_recoverable": Decimal(0)})
    for a, t in scored:
        b = by_reason[t[2]]
        b["n"] += 1
        b["needs_evidence"] += a.verdict == "needs_evidence"
        b["verdict_correct"] += a.verdict != "needs_evidence" and (a.verdict == "valid") == t[0]
        b["true_recoverable"] += t[1]
    mistakes = [{"claim": a.claim_number, "rule": a.rule, "truth": t[2], "verdict": a.verdict,
                 "region": region.get(store.get(a.deduction_id))}
                for a, t in scored if a.verdict != "needs_evidence" and (a.verdict == "valid") != t[0]]
    return {
        **_deduction_block(scored),
        "design_region": {"regions": list(DESIGN_REGIONS), **_deduction_block(
            [(a, t) for a, t in scored if region.get(store.get(a.deduction_id)) in DESIGN_REGIONS])},
        "report_region": {"regions": list(REPORT_REGIONS), **_deduction_block(
            [(a, t) for a, t in scored if region.get(store.get(a.deduction_id)) in REPORT_REGIONS])},
        "mistakes": mistakes,
        "by_reason": {k: {**v, "true_recoverable": str(v["true_recoverable"])} for k, v in sorted(by_reason.items())},
    }


def recovery_simulation(conn: psycopg.Connection, assessments: list[deductions.Assessment],
                        capacities: tuple[int, ...] = (1, 2, 3, 5)) -> list[dict]:
    truth = {d: Decimal(a) - Decimal(v) for d, a, v in conn.execute(
        "SELECT g.deduction_id, d.amount, g.valid_amount FROM eval.gt_deduction g JOIN deduction d USING (deduction_id)")}
    if not assessments:
        return []
    start = min(a.deduction_date for a in assessments)
    end = conn.execute("SELECT max(as_of_date) FROM world_clock").fetchone()[0]
    worth = sum((v for d, v in truth.items() if v > deductions.DISPUTE_HANDLING_COST), Decimal(0))
    out = []
    for cap in capacities:
        row = {"reviews_per_week": cap, "disputable_recoverable": str(worth)}
        for p in deductions.POLICIES:
            r = deductions.simulate_recovery(assessments, truth, p, cap, start, end)
            row[p] = {k: r[k] for k in ("recovered", "disputes", "false_disputes", "recoverable_lost_to_deadline")}
            row[p]["share_of_disputable"] = round(float(Decimal(r["recovered"]) / worth), 4) if worth else None
        out.append(row)
    return out


def phase2a_report(conn: psycopg.Connection) -> dict:
    """Run both engines from a clean slate as cpgos_engine, then score them with the answer key."""
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_engine")
        full = cashapp.match_all(conn, "full", replay=True)
        base = cashapp.match_all(conn, "exact_only", replay=True)
        assessed = deductions.assess_all(conn)
    world = conn.execute(
        """SELECT d.dataset_version_id, d.file_name, w.as_of_date FROM dataset_version d
           JOIN world_clock w USING (dataset_version_id) WHERE d.dataset_key = 'finance_world'
           ORDER BY d.dataset_version_id DESC LIMIT 1""").fetchone()
    return {
        "world": {"dataset_version_id": world[0], "description": world[1], "as_of": str(world[2])} if world else None,
        "cash_application": {"full": score_cash_application(conn, full),
                             "exact_only_baseline": score_cash_application(conn, base),
                             "auto_threshold": 0.90},
        "deductions": score_deductions(conn, assessed),
        "recovery_simulation": recovery_simulation(conn, assessed),
        "assumptions": {"dispute_handling_cost": str(deductions.DISPUTE_HANDLING_COST),
                        "urgent_days": deductions.URGENT_DAYS,
                        "dispute_win": "a disputed claim recovers its true recoverable amount, for every policy alike"},
    }


def save_report(conn: psycopg.Connection, report: dict, code_version: str) -> int:
    import json
    from psycopg.types.json import Jsonb
    body = json.loads(json.dumps(report, default=str))
    ver = report["world"]["dataset_version_id"] if report.get("world") else None
    return conn.execute(
        "INSERT INTO eval.report (kind, dataset_version_id, code_version, body) VALUES ('phase2a', %s, %s, %s) RETURNING report_id",
        (ver, code_version, Jsonb(body))).fetchone()[0]
