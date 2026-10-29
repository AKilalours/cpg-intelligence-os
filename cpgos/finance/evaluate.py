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
