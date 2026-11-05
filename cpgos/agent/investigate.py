"""The investigator: for every claim, accept, dispute with a packet, or ask for one named document.

Analogy: a senior AR analyst going through the engine's work. Where the evidence is complete they
sign off; where the engine is stuck on a gap they know which document closes it; and where the
records say "no" but something on the shelf says otherwise, they ask before disputing, because a
lost dispute costs a relationship and analyst time. Every outcome lists the facts it used, the tool
calls behind them, and the calculation rows for its numbers.

Playbooks were designed on California stores only (the design region) and are scored once on Texas
and Wisconsin, like the engine (docs/phase3_agent.md).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal

import psycopg
from psycopg.types.json import Jsonb

from cpgos.agent.tools import Tools, price_drop_weeks
from cpgos.finance.deductions import _iso_weeks, _verdict

AGENT_VERSION = "investigator-1.0.0"

REQUESTS = {   # what a supplier asks the retailer for, by engine rule
    "scan_back_pos_incomplete": "POS detail for the missing weeks",
    "shortage_no_receipt": "receiving record (signed delivery receipt with counts)",
    "late_fee_no_delivery_record": "proof of delivery with date",
    "spoils_missing_fields": "store and period for the spoils claim",
    "shortage_invoice_not_found": "copy of the invoice the claim refers to",
    "late_fee_invoice_not_found": "copy of the invoice the claim refers to",
}


@dataclass
class Finding:
    deduction_id: int
    claim_number: str
    engine_verdict: str
    engine_rule: str
    action: str                          # accept | dispute | request
    reason: str
    recoverable: Decimal = Decimal(0)    # what a dispute asks back
    request: str | None = None
    facts: list[dict] = field(default_factory=list)
    calculation_ids: list[int] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)


def _one(t: Tools, did: int) -> Finding:
    c, e = t.claim(did), t.engine_assessment(did)
    verdict, rule, ev = e["verdict"], e["rule"], e["evidence"] or {}
    f = Finding(did, c["claim_number"], verdict, rule, "accept", "", Decimal(e["recoverable_amount"]))
    earlier = ev.get("earlier_claims") or []

    if verdict == "valid":
        f.action, f.reason = "accept", "the evidence supports the claim in full"
        return f

    if verdict == "needs_evidence":
        if rule == "scan_back_missing_fields" and c["sku_id"] and c["period_start"] and c["period_end"]:
            return _infer_store(t, c, f)
        f.action, f.request = "request", REQUESTS.get(rule, "the retailer's backup for this claim")
        f.reason = f"the engine could not decide ({rule.replace('_', ' ')}); this document closes the gap"
        if rule == "scan_back_pos_incomplete":
            f.facts.append({"fact": "weeks missing from the POS provided", "value": ev.get("pos_gaps")})
        return f

    # The engine says the claim is not (fully) supported. Check what could make that wrong first.
    if rule == "scan_back_no_agreement" and c["store_id"] and not earlier:
        drops = price_drop_weeks(t.shelf_prices(c["store_id"], c["sku_id"], c["period_start"], c["period_end"]),
                                 c["period_start"], c["period_end"])
        if drops:
            f.action, f.request = "request", "the promotion agreement for this store, item, and period"
            f.reason = "no agreement is on file, but the shelf price dropped like a promotion; ask before disputing"
            f.facts.append({"fact": "promotion-level price weeks in the claimed period", "value": drops})
            return f
        f.facts.append({"fact": "shelf price in the claimed period", "value": "no promotion-level drop"})

    if rule == "scan_back_overclaim" and c["store_id"] and not earlier:
        ends = [p.get("promotion_id") for p in ev.get("promotions") or []]
        promos = [p for p in t.promotions(c["partner_id"], c["sku_id"], c["period_start"], c["period_end"], c["store_id"])
                  if p["promotion_id"] in ends]
        last_end = max((p["end_date"] for p in promos), default=None)
        if last_end is not None and c["period_end"] > last_end:
            drops = price_drop_weeks(t.shelf_prices(c["store_id"], c["sku_id"], c["period_start"], c["period_end"]),
                                     last_end, c["period_end"])
            if drops:
                f.action, f.request = "request", "the amended promotion agreement (end date)"
                f.reason = ("the claim runs past the recorded promotion end, and the shelf price stayed promotional "
                            "after it; the end date on file may be wrong")
                f.facts += [{"fact": "recorded promotion end", "value": str(last_end)},
                            {"fact": "claimed period end", "value": str(c["period_end"])},
                            {"fact": "promotion-level price weeks after the recorded end", "value": drops}]
                return f

    f.action = "dispute"
    f.reason = (f"already claimed on {', '.join(earlier)}" if earlier else
                f"the evidence does not support it ({rule.replace('_', ' ')})")
    f.facts.append({"fact": "engine rule", "value": rule})
    if earlier:
        f.facts.append({"fact": "earlier claims for the same thing", "value": earlier})
    f.calculation_ids.append(t.record("dispute_amount", {"deduction_id": did, "engine_rule": rule,
                                                          "amount": str(c["amount"]),
                                                          "supported": str(e["supported_amount"]),
                                                          "engine_evidence": ev}, f.recoverable))
    return f


def _infer_store(t: Tools, c: dict, f: Finding) -> Finding:
    """A scan-back with no store: if exactly one store ran a promotion for this item in that period, check it."""
    promos = t.promotions(c["partner_id"], c["sku_id"], c["period_start"], c["period_end"])
    stores = sorted({p["store_id"] for p in promos})
    if len(stores) != 1:
        f.action, f.request = "request", "the store this claim is for"
        f.reason = f"the claim has no store and {len(stores)} stores ran a promotion for this item then"
        return f
    store = stores[0]
    weeks = t.pos_weeks(store, c["sku_id"], c["period_start"], c["period_end"])
    earned, parts = Decimal(0), []
    for p in promos:
        lo, hi = max(p["start_date"], c["period_start"]), min(p["end_date"], c["period_end"])
        units = sum(int(w["units"]) for w in weeks if lo <= w["week_start"] <= hi)
        earned += Decimal(p["allowance_per_unit"]) * units
        parts.append({"promotion_id": p["promotion_id"], "allowance_per_unit": str(p["allowance_per_unit"]),
                      "pos_units": units, "from": str(lo), "to": str(hi)})
    expected = sum(_iso_weeks(max(p["start_date"], c["period_start"]), min(p["end_date"], c["period_end"]))
                   for p in promos)
    if len(weeks) < expected:
        f.action, f.request = "request", "POS detail for the missing weeks"
        f.reason = "store inferred, but its POS for the period is incomplete"
        return f
    taken = t.accepted_against([p["promotion_id"] for p in promos], c["claim_number"])
    support = max(earned.quantize(Decimal("0.01")) - taken, Decimal(0))
    verdict, recoverable = _verdict(Decimal(c["amount"]), support)
    cid = t.record("scan_back_support_inferred_store",
                   {"deduction_id": c["deduction_id"], "inferred_store_id": store, "promotions": parts,
                    "already_accepted": str(taken), "amount": str(c["amount"])}, support)
    f.calculation_ids.append(cid)
    f.facts += [{"fact": "store inferred", "value": f"the only store with a promotion for this item then (store {store})"},
                {"fact": "supported by POS at that store", "value": str(support)}]
    f.recoverable = recoverable
    if verdict == "valid":
        f.action, f.reason = "accept", "supported in full once the store is known"
    else:
        f.action, f.reason = "dispute", "not supported even at the only store that ran the promotion"
    return f


def investigate(conn: psycopg.Connection, deduction_ids: list[int] | None = None) -> list[Finding]:
    """Investigate claims as the agent role. Arithmetic is recorded; nothing in the ledger changes."""
    out = []
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_agent")
        ids = deduction_ids or [r[0] for r in conn.execute(
            """SELECT DISTINCT d.deduction_id FROM observed.deduction d JOIN deduction_assessment a USING (deduction_id)
               ORDER BY 1""")]
        for did in ids:
            t = Tools(conn)
            f = _one(t, did)
            f.tool_calls = t.log
            out.append(f)
    return out


def save(conn: psycopg.Connection, findings: list[Finding]) -> int:
    """One case per claim and an audit event with the full reasoning, as the agent role."""
    status = {"accept": "awaiting_approval", "dispute": "awaiting_approval", "request": "awaiting_evidence"}
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_agent")
        for f in findings:
            case = conn.execute("""INSERT INTO investigation_case (case_type, deduction_id, status)
                                   VALUES ('deduction', %s, %s) RETURNING case_id""",
                                (f.deduction_id, status[f.action])).fetchone()[0]
            body = {k: v for k, v in asdict(f).items() if k not in ("calculation_ids",)}
            conn.execute("""INSERT INTO audit_event (case_id, actor_type, actor_id, action, calculation_ids, payload,
                                                     model_version)
                            VALUES (%s, 'agent', %s, 'recommendation', %s, %s, NULL)""",
                         (case, AGENT_VERSION, f.calculation_ids, Jsonb(_json(body))))
    return len(findings)


def _json(x):
    import json
    return json.loads(json.dumps(x, default=str))
