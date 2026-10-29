"""Cash application: decide which invoice each remittance line pays.

Evidence is tried from strongest to weakest. When evidence conflicts or is
weak, the engine abstains (review or unmatched) instead of guessing: a wrong
automatic match silently corrupts the ledger, while an unmatched line is
merely visible work.

`match_all` reads only and returns decisions, so different strategies can be
scored on the same inputs. `apply` writes them, as the cpgos_engine role.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import psycopg
from psycopg.types.json import Jsonb

ENGINE_VERSION = "cashapp-1.0.0"
AMOUNT_WINDOW_DAYS = 90       # amount-only matches must be invoices from the last 90 days
STRATEGIES = ("full", "exact_only")


@dataclass
class Match:
    remittance_line_id: int
    payment_date: date
    amount: Decimal
    raw_ref: str | None
    invoice_id: int | None
    method: str | None            # 'exact' | 'rule'
    confidence: float
    decision: str                 # 'auto' | 'review' | 'unmatched'
    explanation: dict = field(default_factory=dict)


def normalize_ref(ref: str | None) -> str | None:
    if ref is None:
        return None
    s = re.sub(r"[^A-Za-z0-9]", "", ref).upper()
    return s or None


def within_one_edit(a: str, b: str) -> bool:
    """True if a and b differ by one substitution, insertion, deletion, or adjacent swap."""
    if a == b:
        return False
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diff = [i for i in range(la) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]
    if la > lb:
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


def _load(conn: psycopg.Connection, partner_id: int | None, replay: bool = False):
    """replay=True ignores decisions already in the ledger, so evaluation can re-run
    the engine from a clean slate after `make reconcile` has written its decisions."""
    where = "AND p.partner_id = %(partner)s" if partner_id else ""
    decided = "" if replay else """
          AND NOT EXISTS (SELECT 1 FROM cash_application c
                          WHERE c.remittance_line_id = r.remittance_line_id AND c.status IN ('proposed', 'approved'))"""
    applied = "0" if replay else """coalesce((SELECT sum(applied_amount) FROM cash_application c
                                          WHERE c.invoice_id = i.invoice_id AND c.status = 'approved'), 0)"""
    lines = conn.execute(f"""
        SELECT r.remittance_line_id, p.payment_date, r.net_amount, r.raw_invoice_ref, p.partner_id
        FROM remittance_line r JOIN payment p USING (payment_id)
        WHERE r.raw_code IS NULL AND r.deduction_amount = 0 AND r.net_amount > 0 {where}{decided}
        ORDER BY p.payment_date, r.payment_id, r.line_number""", {"partner": partner_id}).fetchall()
    invoices = conn.execute(f"""
        SELECT i.invoice_id, i.invoice_number, i.partner_id, i.invoice_date,
               i.total_amount - {applied}
        FROM invoice i {"WHERE i.partner_id = %(partner)s" if partner_id else ""}""", {"partner": partner_id}).fetchall()
    return lines, invoices


def match_all(conn: psycopg.Connection, strategy: str = "full", partner_id: int | None = None,
              auto_threshold: float = 0.90, replay: bool = False) -> list[Match]:
    if strategy not in STRATEGIES:
        raise ValueError(f"strategy must be one of {STRATEGIES}")
    lines, invoices = _load(conn, partner_id, replay)
    by_ref: dict[tuple[int, str], int] = {}
    info: dict[int, tuple[str, int, date]] = {}
    balance: dict[int, Decimal] = {}
    known_refs: set[tuple[int, str]] = set()
    for inv_id, number, partner, inv_date, bal in invoices:
        by_ref[(partner, normalize_ref(number))] = inv_id
        known_refs.add((partner, normalize_ref(number)))
        info[inv_id] = (normalize_ref(number), partner, inv_date)
        balance[inv_id] = Decimal(bal)

    out: list[Match] = []
    for rl_id, pay_date, amount, raw_ref, partner in lines:
        amount = Decimal(amount)
        ref = normalize_ref(raw_ref)
        m = Match(rl_id, pay_date, amount, raw_ref, None, None, 0.0, "unmatched")

        def open_with_amount(exclude: int | None = None) -> list[int]:
            lo = pay_date - timedelta(days=AMOUNT_WINDOW_DAYS)
            return [i for i, (n, p, d) in info.items()
                    if p == partner and i != exclude and balance[i] == amount and lo <= d <= pay_date]

        exact = by_ref.get((partner, ref)) if ref else None
        if exact is not None and balance[exact] <= 0 and strategy == "full":
            # The reference names a real invoice that is already fully applied: a duplicate
            # payment, or an earlier wrong match. Never "correct" it to a neighbouring number.
            m.invoice_id, m.method, m.confidence = exact, "exact", 0.50
            m.explanation = {"reference": "exact, but that invoice is already fully applied",
                             "risk": "duplicate payment or an earlier wrong match"}
        elif exact is not None and balance[exact] > 0:
            bal = balance[exact]
            if amount == bal:
                m.invoice_id, m.method, m.confidence = exact, "exact", 0.99
                m.explanation = {"reference": "exact", "amount": "equals open balance"}
            elif strategy == "exact_only":
                if amount < bal:
                    m.invoice_id, m.method, m.confidence = exact, "exact", 0.90
                    m.explanation = {"reference": "exact", "amount": "partial payment"}
            else:
                alt = open_with_amount(exclude=exact)
                if len(alt) == 1:
                    # The reference says one invoice; the amount exactly fits a different one.
                    m.invoice_id, m.method, m.confidence = alt[0], "rule", 0.60
                    m.explanation = {"reference": "points to a different invoice", "amount": "exactly equals this invoice's open balance",
                                     "referenced_invoice_id": exact, "conflict": True}
                elif amount < bal:
                    # A short-pay. Indistinguishable from a wrong reference to a larger invoice,
                    # so a person decides; finance teams review short-pays anyway.
                    m.invoice_id, m.method, m.confidence = exact, "exact", 0.80
                    m.explanation = {"reference": "exact", "amount": "less than open balance (short-pay)", "open_balance": str(bal),
                                     "other_invoices_with_this_amount": len(alt)}
                else:
                    m.explanation = {"reference": "exact", "amount": "exceeds open balance", "open_balance": str(bal)}
        elif strategy == "full":
            # Fuzzy matching only when the reference names no invoice at all: invoice numbers
            # are sequential, so a real reference is often one edit away from a real neighbour.
            unknown = ref is not None and (partner, ref) not in known_refs
            near = [i for i, (n, p, _) in info.items()
                    if unknown and p == partner and balance[i] > 0 and within_one_edit(ref, n)]
            near_amount = [i for i in near if balance[i] == amount]
            if len(near_amount) == 1:
                m.invoice_id, m.method, m.confidence = near_amount[0], "rule", 0.95
                m.explanation = {"reference": "one edit from invoice number (likely typo)", "amount": "equals open balance"}
            elif len(near) == 1:
                m.invoice_id, m.method, m.confidence = near[0], "rule", 0.75
                m.explanation = {"reference": "one edit from invoice number (likely typo)", "amount": "differs from open balance"}
            else:
                alt = open_with_amount()
                if len(alt) == 1:
                    m.invoice_id, m.method, m.confidence = alt[0], "rule", 0.85
                    m.explanation = {"reference": "missing or unrecognized", "amount": "uniquely equals an open balance in the last 90 days"}
                elif len(alt) > 1:
                    m.explanation = {"reference": "missing or unrecognized", "amount": f"matches {len(alt)} open invoices; ambiguous"}
                else:
                    m.explanation = {"reference": "missing or unrecognized", "amount": "matches no open invoice"}

        if m.invoice_id is not None:
            m.decision = "auto" if m.confidence >= auto_threshold else "review"
            if m.decision == "auto":
                balance[m.invoice_id] -= amount   # later lines see the reduced balance
        out.append(m)
    return out


def apply(conn: psycopg.Connection, matches: list[Match]) -> dict:
    """Write decisions as the engine role: auto -> approved, review -> proposed."""
    counts = {"auto": 0, "review": 0, "unmatched": 0}
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_engine")
        for m in matches:
            counts[m.decision] += 1
            if m.invoice_id is None:
                continue
            conn.execute(
                """INSERT INTO cash_application (remittance_line_id, invoice_id, applied_amount, method, confidence,
                                                 match_explanation, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (m.remittance_line_id, m.invoice_id, m.amount, m.method, round(m.confidence, 4),
                 Jsonb({**m.explanation, "engine": ENGINE_VERSION}), "approved" if m.decision == "auto" else "proposed"),
            )
        conn.execute(
            """UPDATE invoice i SET status = CASE WHEN x.applied >= i.total_amount THEN 'paid' ELSE 'partially_paid' END
               FROM (SELECT invoice_id, sum(applied_amount) AS applied FROM cash_application
                     WHERE status = 'approved' GROUP BY invoice_id) x
               WHERE x.invoice_id = i.invoice_id AND i.status IN ('open', 'partially_paid')""")
    return counts
