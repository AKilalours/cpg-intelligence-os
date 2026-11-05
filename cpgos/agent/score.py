"""Score the investigator against the hidden answer key, beside the engine it builds on.

Read with the answer key, as the evaluator; the agent itself never can (migration 0019). Outcomes:
a dispute of a valid claim is a false dispute; accepting an invalid claim is a false approval; a
request is neither right nor wrong on its own, so requests are counted, with how many were for claims
that turned out valid (requests that prevented a false dispute) and invalid (a slower dispute).
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

import psycopg

from cpgos.agent.investigate import AGENT_VERSION, Finding
from cpgos.finance.evaluate import DESIGN_REGIONS, REPORT_REGIONS, rate


def _block(rows: list[tuple[Finding, tuple]]) -> dict:
    """rows: (finding, (is_valid, true_recoverable, reason, engine_verdict))."""
    acc = [(f, t) for f, t in rows if f.action == "accept"]
    dis = [(f, t) for f, t in rows if f.action == "dispute"]
    req = [(f, t) for f, t in rows if f.action == "request"]
    eng_dispute = [(f, t) for f, t in rows if f.engine_verdict in ("invalid", "partially_valid")]
    eng_accept = [(f, t) for f, t in rows if f.engine_verdict == "valid"]
    return {
        "claims": len(rows),
        "actions": dict(Counter(f.action for f, _ in rows)),
        "false_approvals": rate(sum(not t[0] for _, t in acc), len(acc)),
        "false_disputes": rate(sum(t[0] for _, t in dis), len(dis)),
        "disputes_exact_to_the_cent": rate(sum((not t[0]) and f.recoverable == t[1] for f, t in dis), len(dis)),
        "recoverable_disputed_correctly": str(sum((t[1] for f, t in dis if not t[0]), Decimal(0))),
        "true_recoverable_total": str(sum((t[1] for _, t in rows if not t[0]), Decimal(0))),
        "requests": {"n": len(req), "claim_was_valid": sum(t[0] for _, t in req),
                     "claim_was_invalid": sum(not t[0] for _, t in req),
                     "by_document": dict(Counter(f.request for f, _ in req))},
        "engine": {"false_approvals": rate(sum(not t[0] for _, t in eng_accept), len(eng_accept)),
                   "false_disputes": rate(sum(t[0] for _, t in eng_dispute), len(eng_dispute)),
                   "needs_evidence": sum(f.engine_verdict == "needs_evidence" for f, _ in rows)},
    }


def score(conn: psycopg.Connection, findings: list[Finding]) -> dict:
    truth = {d: (v, Decimal(a) - Decimal(va), r) for d, v, va, r, a in conn.execute(
        """SELECT g.deduction_id, g.is_valid, g.valid_amount, g.reason, d.amount
           FROM eval.gt_deduction g JOIN deduction d USING (deduction_id)""")}
    store = dict(conn.execute("SELECT deduction_id, store_id FROM deduction"))
    region = {sid: code[:2] for sid, code in conn.execute("SELECT store_id, external_code FROM store")}
    rows = [(f, truth[f.deduction_id]) for f in findings if f.deduction_id in truth]
    reg = lambda f: region.get(store.get(f.deduction_id))
    changed = [{"claim": f.claim_number, "region": reg(f), "engine": f.engine_verdict, "agent": f.action,
                "request": f.request, "truth": "valid" if t[0] else t[2], "reason": f.reason}
               for f, t in rows if not ((f.action == "accept" and f.engine_verdict == "valid") or
                                        (f.action == "dispute" and f.engine_verdict in ("invalid", "partially_valid")))]
    return {"agent": AGENT_VERSION, "all": _block(rows),
            "design_region": {"regions": list(DESIGN_REGIONS), **_block([(f, t) for f, t in rows if reg(f) in DESIGN_REGIONS])},
            "report_region": {"regions": list(REPORT_REGIONS), **_block([(f, t) for f, t in rows if reg(f) in REPORT_REGIONS])},
            "agent_differs_from_engine": changed[:60]}
