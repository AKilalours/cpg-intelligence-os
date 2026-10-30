"""Deduction validation and recovery prioritization.

Every verdict comes from records (schema "observed": what the business can see), never from a model: the promotion agreement
and retailer POS for scan-backs, the receipt for shortages, the shipment for
late fees, and invoiced value for spoils. Each assessment carries the record
ids and values it used, so a reviewer (or the agent in Phase 3) can check the
arithmetic line by line.

The analogy: a deduction is a customer saying "you owe us this". The validator
is the clerk who pulls the contract, the delivery slip, and the till receipts
before anyone argues, and writes down exactly which page proves what.

The engine runs as cpgos_engine, which cannot read the eval schema, so the
answer key cannot leak into a verdict.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import psycopg
from psycopg.types.json import Jsonb

from cpgos.money import Money

ENGINE_VERSION = "deductions-2.0.0"

# Assumptions, stated where they are used and shown in the docs.
DISPUTE_HANDLING_COST = Decimal("25.00")   # analyst time per dispute; below this a dispute loses money
# Retailer POS feeds get restated after the fact by a few percent. A scan-back claim within this
# tolerance of what the POS supports is accepted, not disputed. Chosen on California stores only;
# reported on Texas and Wisconsin (see docs/phase2a_reconciliation.md).
SCAN_BACK_TOLERANCE_PCT = Decimal("0.03")   # restatements are at most 3% (evidence.py); over-claims are 10-40%
# Only cent rounding below that. A $1.00 floor (first version) approved four over-claims of 20-100%
# on claims under $4 in the design region.
SCAN_BACK_TOLERANCE_MIN = Decimal("0.05")
URGENT_DAYS = 21                            # claims this close to the dispute deadline jump the queue


@dataclass
class Assessment:
    deduction_id: int
    claim_number: str
    code: str
    reason_text: str
    amount: Decimal
    deduction_date: date
    dispute_deadline: date | None
    verdict: str
    rule: str
    supported: Decimal | None
    recoverable: Decimal
    evidence: dict = field(default_factory=dict)

    @property
    def worth_disputing(self) -> bool:
        return self.recoverable > DISPUTE_HANDLING_COST

    def days_to_deadline(self, today: date) -> int | None:
        return (self.dispute_deadline - today).days if self.dispute_deadline else None


def _verdict(amount: Decimal, supported: Decimal) -> tuple[str, Decimal]:
    supported = min(max(supported, Decimal(0)), amount)
    recoverable = amount - supported
    if recoverable == 0:
        return "valid", recoverable
    if supported == 0:
        return "invalid", recoverable
    return "partially_valid", recoverable


def _kind(code: str, reason: str) -> str:
    r = (reason or "").upper()
    if code == "WP" or "SCANBACK" in r or "SCAN BACK" in r:
        return "scan_back"
    if code == "WG" or "SPOIL" in r:
        return "spoils"
    if "SHORT" in r:
        return "shortage"
    if "LATE" in r:
        return "late_fee"
    return "unknown"


def _iso_weeks(lo: date, hi: date) -> int:
    """ISO weeks (Monday starts) whose start falls in [lo, hi]."""
    first = lo + timedelta(days=(7 - lo.weekday()) % 7)
    return 0 if first > hi else (hi - first).days // 7 + 1


def assess_all(conn: psycopg.Connection, partner_id: int | None = None) -> list[Assessment]:
    """Assess every deduction in claim order. Order matters: the first claim
    against a promotion, receipt line, or shipment uses up the support for it,
    so a later duplicate is found by arithmetic rather than by string matching."""
    terms = defaultdict(dict)
    for pid, key, value in conn.execute("SELECT partner_id, term_key, value FROM trade_term"):
        terms[pid][key] = value

    rows = conn.execute(
        """SELECT d.deduction_id, d.claim_number, d.partner_id, coalesce(c.code, ''), coalesce(d.raw_reason, ''),
                  d.amount, d.deduction_date, d.dispute_deadline, d.raw_invoice_ref, d.sku_id, d.claimed_units,
                  d.period_start, d.period_end, d.store_id
           FROM observed.deduction d LEFT JOIN deduction_code c USING (deduction_code_id)
           WHERE %(p)s::bigint IS NULL OR d.partner_id = %(p)s
           ORDER BY d.deduction_date, d.deduction_id""", {"p": partner_id}).fetchall()

    used_promo: dict[int, Decimal] = defaultdict(Decimal)      # allowance already accepted per promotion
    used_line: dict[int, Decimal] = defaultdict(Decimal)       # shortage dollars accepted per invoice line
    used_fee: dict[int, Decimal] = defaultdict(Decimal)        # late fees accepted per invoice
    used_spoils: dict[tuple, Decimal] = defaultdict(Decimal)   # spoils accepted per store and period

    out = []
    for (did, claim, partner, code, reason, amount, ddate, deadline, inv_ref, sku, units,
         p0, p1, store) in rows:
        amount = Decimal(amount)
        units = int(units) if units is not None else None
        a = Assessment(did, claim, code, reason, amount, ddate, deadline, "needs_evidence", "", None, Decimal(0))
        kind = _kind(code, reason)
        t = terms.get(partner, {})

        if kind == "scan_back":
            if not (store and sku and p0 and p1):
                a.rule, a.evidence = "scan_back_missing_fields", {"missing": "store, sku, or period"}
            else:
                promos = conn.execute(
                    """SELECT p.promotion_id, p.start_date, p.end_date, p.allowance_per_unit
                       FROM observed.promotion p JOIN observed.promotion_sku ps USING (promotion_id)
                       WHERE p.partner_id = %s AND p.store_id = %s AND ps.sku_id = %s
                         AND p.start_date <= %s AND p.end_date >= %s
                       ORDER BY p.start_date""", (partner, store, sku, p1, p0)).fetchall()
                if not promos:
                    a.verdict, a.recoverable, a.supported = "invalid", amount, Decimal(0)
                    a.rule = "scan_back_no_agreement"
                    a.evidence = {"searched": {"store_id": store, "sku_id": sku, "period": [str(p0), str(p1)]},
                                  "promotions_found": 0}
                else:
                    support, used, gaps = Money.of(0), [], []
                    for pid, s0, s1, allowance in promos:
                        lo, hi = max(s0, p0), min(s1, p1)
                        pos_units, weeks_found = conn.execute(
                            """SELECT coalesce(sum(units), 0), count(*) FROM observed.retailer_pos_week
                               WHERE store_id = %s AND sku_id = %s AND week_start BETWEEN %s AND %s""",
                            (store, sku, lo, hi)).fetchone()
                        weeks_expected = _iso_weeks(lo, hi)
                        if weeks_found < weeks_expected:
                            gaps.append({"promotion_id": pid, "weeks_expected": weeks_expected, "weeks_in_pos": weeks_found})
                        earned = Money.of(allowance).times(int(pos_units))
                        remaining = earned.amount - used_promo[pid]
                        support = support + Money.of(max(remaining, Decimal(0)))
                        used.append({"promotion_id": pid, "allowance_per_unit": str(allowance), "pos_units": int(pos_units),
                                     "earned": str(earned.amount), "already_accepted": str(used_promo[pid])})
                    a.supported = support.amount
                    if gaps:
                        # A missing POS week makes a valid claim look over-claimed: ask for the data instead.
                        a.verdict, a.recoverable, a.rule = "needs_evidence", Decimal(0), "scan_back_pos_incomplete"
                        a.evidence = {"promotions": used, "pos_gaps": gaps, "claimed_amount": str(amount)}
                        out.append(a)
                        continue
                    a.verdict, a.recoverable = _verdict(amount, a.supported)
                    tol = max(SCAN_BACK_TOLERANCE_MIN, (amount * SCAN_BACK_TOLERANCE_PCT).quantize(Decimal("0.01")))
                    within = Decimal(0) < a.recoverable <= tol
                    if within:
                        a.verdict, a.recoverable = "valid", Decimal(0)
                    take = amount - a.recoverable
                    for u in used:                          # consume support in promotion order
                        room = Decimal(u["earned"]) - Decimal(u["already_accepted"])
                        step = min(max(room, Decimal(0)), take)
                        used_promo[u["promotion_id"]] += step
                        take -= step
                    a.rule = ("scan_back_duplicate" if a.verdict == "invalid" and any(Decimal(u["already_accepted"]) > 0 for u in used)
                              else "scan_back_overclaim" if a.recoverable > 0
                              else "scan_back_within_tolerance" if within else "scan_back_supported")
                    a.evidence = {"promotions": used, "claimed_units": units, "claimed_amount": str(amount),
                                  "supported_amount": str(a.supported)}

        elif kind in ("shortage", "late_fee"):
            inv = conn.execute(
                "SELECT invoice_id, invoice_number FROM invoice WHERE partner_id = %s AND upper(invoice_number) = upper(%s)",
                (partner, inv_ref or "")).fetchone()
            if inv is None:
                a.rule, a.evidence = f"{kind}_invoice_not_found", {"raw_invoice_ref": inv_ref}
            elif kind == "shortage":
                line = conn.execute(
                    """SELECT il.invoice_line_id, il.quantity, il.unit_price, r.units_received
                       FROM invoice_line il LEFT JOIN observed.receipt_line r USING (invoice_line_id)
                       WHERE il.invoice_id = %s AND il.sku_id = %s""", (inv[0], sku)).fetchone()
                if line is None or line[3] is None:
                    a.rule = "shortage_no_receipt"
                    a.evidence = {"invoice_id": inv[0], "sku_id": sku, "receipt_found": False}
                else:
                    lid, qty, price, received = line
                    short_units = max(int(qty) - int(received), 0)
                    earned = Money.of(price).times(short_units).amount
                    a.supported = max(earned - used_line[lid], Decimal(0))
                    a.verdict, a.recoverable = _verdict(amount, a.supported)
                    used_line[lid] += amount - a.recoverable
                    a.rule = "shortage_not_on_receipt" if short_units == 0 else (
                        "shortage_exceeds_receipt" if a.recoverable > 0 else "shortage_matches_receipt")
                    a.evidence = {"invoice_id": inv[0], "invoice_number": inv[1], "invoice_line_id": lid,
                                  "shipped_units": int(qty), "received_units": int(received), "unit_price": str(price),
                                  "claimed_units": units, "supported_amount": str(a.supported)}
            else:
                ship = conn.execute("SELECT scheduled_date, delivered_date FROM observed.shipment WHERE invoice_id = %s",
                                    (inv[0],)).fetchone()
                if ship is None or ship[1] is None:
                    a.rule, a.evidence = "late_fee_no_delivery_record", {"invoice_id": inv[0]}
                else:
                    sched, delivered = ship
                    fee = Decimal(str(t.get("late_shipment_fee", "0")))
                    late_days = (delivered - sched).days
                    a.supported = max((fee if late_days > 0 else Decimal(0)) - used_fee[inv[0]], Decimal(0))
                    a.verdict, a.recoverable = _verdict(amount, a.supported)
                    used_fee[inv[0]] += amount - a.recoverable
                    a.rule = "late_fee_on_time_delivery" if late_days <= 0 else (
                        "late_fee_above_published_fee" if a.recoverable > 0 else "late_fee_supported")
                    a.evidence = {"invoice_id": inv[0], "invoice_number": inv[1], "scheduled": str(sched),
                                  "delivered": str(delivered), "days_late": late_days, "published_fee": str(fee)}

        elif kind == "spoils":
            pct = t.get("spoils_allowance_pct")
            if not (store and p0 and p1) or pct is None:
                a.rule, a.evidence = "spoils_missing_fields", {"store_id": store, "period": [str(p0), str(p1)],
                                                               "pct": None if pct is None else str(pct)}
            else:
                value = conn.execute(
                    """SELECT coalesce(sum(total_amount), 0) FROM invoice
                       WHERE partner_id = %s AND store_id = %s AND invoice_date BETWEEN %s AND %s""",
                    (partner, store, p0, p1)).fetchone()[0]
                allowed = Money.of(value).times(Decimal(str(pct))).amount
                key = (store, p0, p1)
                a.supported = max(allowed - used_spoils[key], Decimal(0))
                a.verdict, a.recoverable = _verdict(amount, a.supported)
                used_spoils[key] += amount - a.recoverable
                a.rule = "spoils_over_allowance" if a.recoverable > 0 else "spoils_within_allowance"
                a.evidence = {"store_id": store, "period": [str(p0), str(p1)], "invoiced_value": str(value),
                              "allowance_pct": str(pct), "allowed_amount": str(allowed)}
        else:
            a.rule, a.evidence = "unknown_deduction_type", {"code": code, "reason": reason}
        out.append(a)
    return out


def priority_key(a: Assessment, today: date):
    """Sort key for the dispute queue: urgent claims worth disputing first, then by
    recoverable dollars. Claims past their deadline or not worth a dispute sink."""
    days = a.days_to_deadline(today)
    expired = days is not None and days < 0
    disputable = a.worth_disputing and not expired
    urgent = disputable and days is not None and days <= URGENT_DAYS
    return (not disputable, not urgent, -a.recoverable, days if days is not None else 10**6, a.deduction_id)


def queue(assessments: list[Assessment], today: date) -> list[Assessment]:
    return sorted(assessments, key=lambda a: priority_key(a, today))


POLICIES = ("engine", "largest_amount", "oldest_first")


def simulate_recovery(assessments: list[Assessment], truth_recoverable: dict[int, Decimal], policy: str,
                      reviews_per_week: int, start: date, end: date) -> dict:
    """Replay the period week by week with a fixed review capacity.

    Every policy uses the same validator on the claims it reviews; only the order
    differs, so the result isolates the value of prioritization. A reviewed claim
    the validator finds recoverable is disputed and assumed to recover the true
    recoverable amount (capped at the claimed recoverable). That win assumption is
    the same for every policy and is stated in the docs.
    """
    if policy not in POLICIES:
        raise ValueError(policy)
    pending = sorted(assessments, key=lambda a: a.deduction_date)
    seen: set[int] = set()
    recovered, disputes, false_disputes, lost = Decimal(0), 0, 0, Decimal(0)
    day = start
    while day <= end:
        open_ = [a for a in pending if a.deduction_date <= day and a.deduction_id not in seen
                 and (a.dispute_deadline is None or a.dispute_deadline >= day)]
        if policy == "engine":
            order = queue(open_, day)
        elif policy == "largest_amount":
            order = sorted(open_, key=lambda a: (-a.amount, a.deduction_id))
        else:
            order = sorted(open_, key=lambda a: (a.deduction_date, a.deduction_id))
        for a in order[:reviews_per_week]:
            seen.add(a.deduction_id)
            if a.worth_disputing:
                disputes += 1
                got = min(a.recoverable, truth_recoverable.get(a.deduction_id, Decimal(0)))
                recovered += got
                false_disputes += truth_recoverable.get(a.deduction_id, Decimal(0)) == 0
        day += timedelta(days=7)
    for a in assessments:
        if a.deduction_id not in seen and a.dispute_deadline is not None and a.dispute_deadline < end:
            lost += truth_recoverable.get(a.deduction_id, Decimal(0))
    return {"policy": policy, "reviews_per_week": reviews_per_week, "recovered": str(Money.of(recovered).amount),
            "disputes": disputes, "false_disputes": false_disputes,
            "recoverable_lost_to_deadline": str(Money.of(lost).amount)}


def save(conn: psycopg.Connection, assessments: list[Assessment]) -> int:
    """Write assessments and their calculations as the engine role.

    Idempotent: a deduction whose latest assessment came from this engine version
    with the same verdict, amounts, and evidence is skipped, so re-running
    reconcile adds nothing. A new engine version or new evidence writes a new row.
    Returns the number of assessments written.
    """
    import json
    latest = {r[0]: r[1:] for r in conn.execute(
        """SELECT DISTINCT ON (deduction_id) deduction_id, engine_version, verdict, supported_amount,
                  recoverable_amount, evidence
           FROM deduction_assessment ORDER BY deduction_id, assessment_id DESC""")}

    def same(a: Assessment) -> bool:
        prev = latest.get(a.deduction_id)
        canon = lambda e: json.dumps(e, sort_keys=True, default=str)
        return prev is not None and prev[0] == ENGINE_VERSION and prev[1] == a.verdict \
            and prev[2] == a.supported and prev[3] == a.recoverable and canon(prev[4]) == canon(a.evidence)

    todo = [a for a in assessments if not same(a)]
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_engine")
        for a in todo:
            calc = conn.execute(
                """INSERT INTO calculation (kind, formula_version, inputs, result, currency, computed_by)
                   VALUES ('deduction_recoverable', %s, %s, %s, 'USD', 'cpgos.finance.deductions.assess_all')
                   RETURNING calculation_id""",
                (ENGINE_VERSION, Jsonb({"deduction_id": a.deduction_id, "amount": str(a.amount), **a.evidence}),
                 a.recoverable)).fetchone()[0]
            conn.execute(
                """INSERT INTO deduction_assessment (deduction_id, engine_version, verdict, rule, supported_amount,
                       recoverable_amount, evidence, calculation_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (a.deduction_id, ENGINE_VERSION, a.verdict, a.rule, a.supported, a.recoverable, Jsonb(a.evidence), calc))
            conn.execute(
                """INSERT INTO audit_event (actor_type, actor_id, action, payload)
                   VALUES ('system', %s, 'deduction_assessed', %s)""",
                (ENGINE_VERSION, Jsonb({"deduction_id": a.deduction_id, "verdict": a.verdict,
                                        "recoverable": str(a.recoverable)})))
    return len(todo)
