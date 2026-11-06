"""People deciding on cash matches and deductions (fix 4, finance half).

Same rules as document review: a named reviewer, the database's reviewer role
(the only role allowed to change these statuses), an audit event for every
decision, and no decision taken twice. The engine's recommendation is stored
next to the person's decision, so disagreements with the engine are visible.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb


class DecisionError(ValueError):
    pass


def _q(conn, sql, params=()):
    return conn.cursor(row_factory=tuple_row).execute(sql, params)


def _who(reviewer: str) -> str:
    if not reviewer or not reviewer.strip():
        raise DecisionError("a reviewer name is required: every decision is attributed")
    return reviewer.strip()


def decide_cash(conn: psycopg.Connection, remittance_line_id: int, action: str, reviewer: str,
                note: str | None = None) -> dict:
    """approve: post the suggested match. reject: the suggestion is wrong; the cash stays unapplied."""
    if action not in ("approve", "reject"):
        raise DecisionError("action must be approve or reject")
    who = _who(reviewer)
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_reviewer")
        row = _q(conn, """SELECT cash_application_id, status, invoice_id, applied_amount, confidence, lane, routed
                          FROM cash_application
                          WHERE remittance_line_id = %s ORDER BY cash_application_id DESC LIMIT 1""",
                 (remittance_line_id,)).fetchone()
        if row is None:
            raise DecisionError(f"no suggested match for cash line {remittance_line_id}")
        cid, status, invoice_id, amount, confidence, lane, routed = row
        if status != "proposed":
            raise DecisionError(f"this match was already {status}")
        new = "approved" if action == "approve" else "rejected"
        _q(conn, "UPDATE cash_application SET status = %s WHERE cash_application_id = %s", (new, cid))
        if new == "approved":                      # posting cash pays the invoice, as automatic posting does
            _q(conn, """UPDATE invoice i SET status = CASE WHEN x.applied >= i.total_amount THEN 'paid'
                                                           ELSE 'partially_paid' END
                        FROM (SELECT sum(applied_amount) AS applied FROM cash_application
                              WHERE status = 'approved' AND invoice_id = %s) x
                        WHERE i.invoice_id = %s AND i.status IN ('open', 'partially_paid')""", (invoice_id, invoice_id))
        _q(conn, "INSERT INTO audit_event (actor_type, actor_id, action, payload) VALUES ('human', %s, %s, %s)",
           (who, f"cash_{action}", Jsonb({"cash_application_id": cid, "remittance_line_id": remittance_line_id,
                                           "invoice_id": invoice_id, "amount": str(amount),
                                           "engine_confidence": None if confidence is None else str(confidence),
                                           "lane": lane, "routed": routed, "note": note})))
    return {"cash_application_id": cid, "remittance_line_id": remittance_line_id, "status": new,
            "counts_as_evidence": routed == "audit"}


def reverse_cash(conn: psycopg.Connection, remittance_line_id: int, reviewer: str, note: str) -> dict:
    """A posted match turned out wrong (the customer says so, or collections finds it). Undo the
    posting, reopen the invoice, and, if the engine posted it on its own, stop that lane: a wrong
    automatic posting that got through is exactly what the error budget exists to prevent."""
    who = _who(reviewer)
    if not note or not note.strip():
        raise DecisionError("say why the posting is wrong: a reversal is evidence against the engine")
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_reviewer")
        row = _q(conn, """SELECT cash_application_id, status, invoice_id, applied_amount, lane, routed, engine_version
                          FROM cash_application WHERE remittance_line_id = %s
                          ORDER BY cash_application_id DESC LIMIT 1""", (remittance_line_id,)).fetchone()
        if row is None:
            raise DecisionError(f"no match for cash line {remittance_line_id}")
        cid, status, invoice_id, amount, lane, routed, version = row
        if status != "approved":
            raise DecisionError(f"only a posted match can be reversed; this one is {status}")
        _q(conn, "UPDATE cash_application SET status = 'reversed', reversed_at = clock_timestamp() "
                 "WHERE cash_application_id = %s", (cid,))
        _q(conn, """UPDATE invoice i SET status = CASE WHEN x.applied >= i.total_amount THEN 'paid'
                                                    WHEN x.applied > 0 THEN 'partially_paid' ELSE 'open' END
                    FROM (SELECT coalesce(sum(applied_amount), 0) AS applied FROM cash_application
                          WHERE invoice_id = %s AND status = 'approved') x
                    WHERE i.invoice_id = %s""", (invoice_id, invoice_id))
        _q(conn, "INSERT INTO audit_event (actor_type, actor_id, action, payload) VALUES ('human', %s, 'cash_reverse', %s)",
           (who, Jsonb({"cash_application_id": cid, "remittance_line_id": remittance_line_id, "invoice_id": invoice_id,
                        "amount": str(amount), "lane": lane, "routed": routed, "engine_version": version,
                        "note": note.strip()})))
    return {"cash_application_id": cid, "status": "reversed", "stops_lane": lane if routed == "auto" else None}


def decide_deduction(conn: psycopg.Connection, deduction_id: int, action: str, reviewer: str,
                     note: str | None = None) -> dict:
    """dispute: send it back to the retailer. accept: the claim is valid; write it off as agreed."""
    if action not in ("dispute", "accept"):
        raise DecisionError("action must be dispute or accept")
    who = _who(reviewer)
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_reviewer")
        row = _q(conn, "SELECT status, amount, dispute_deadline FROM deduction WHERE deduction_id = %s",
                 (deduction_id,)).fetchone()
        if row is None:
            raise DecisionError(f"no deduction {deduction_id}")
        status, amount, deadline = row
        if status not in ("open", "investigating"):
            raise DecisionError(f"this deduction is already {status.replace('_', ' ')}")
        engine = _q(conn, """SELECT verdict, recoverable_amount, rule FROM deduction_assessment WHERE deduction_id = %s
                             ORDER BY assessment_id DESC LIMIT 1""", (deduction_id,)).fetchone()
        new = "disputed" if action == "dispute" else "valid_accepted"
        _q(conn, "UPDATE deduction SET status = %s WHERE deduction_id = %s", (new, deduction_id))
        agrees = None if engine is None else (
            (action == "dispute") == (engine[0] in ("invalid", "partially_valid")))
        _q(conn, "INSERT INTO audit_event (actor_type, actor_id, action, payload) VALUES ('human', %s, %s, %s)",
           (who, f"deduction_{action}", Jsonb({"deduction_id": deduction_id, "amount": str(amount),
                                                "deadline": None if deadline is None else str(deadline),
                                                "engine_verdict": engine[0] if engine else None,
                                                "engine_recoverable": str(engine[1]) if engine and engine[1] is not None else None,
                                                "engine_rule": engine[2] if engine else None,
                                                "agrees_with_engine": agrees, "note": note})))
    return {"deduction_id": deduction_id, "status": new, "agrees_with_engine": agrees}
