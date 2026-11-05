"""The reviewer's side of an investigation: the queue, one case in full, and the decision.

The agent recommends; a person decides. Approving a dispute or an acceptance changes the deduction
through the same reviewer path as the Cash & deductions page (audited, never twice). Marking a
request as sent keeps the claim open. Every decision is an audit event on the case, recording
whether the person followed the agent.
"""

from __future__ import annotations

from decimal import Decimal

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from cpgos.agent import letter
from cpgos.finance import review as finance_review


class CaseError(ValueError):
    pass


def _exact(x):
    """Money stays an exact decimal string all the way to the screen. A plain dict returned by FastAPI
    would otherwise turn Decimal into a float (found 2026-11-05: the case screen crashed on a float amount)."""
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, dict):
        return {k: _exact(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_exact(v) for v in x]
    return x


def _rows(conn, sql, params=()):
    return [_exact(r) for r in conn.cursor(row_factory=dict_row).execute(sql, params).fetchall()]


_LATEST = """
    WITH c AS (SELECT DISTINCT ON (deduction_id) case_id, deduction_id, status, opened_at FROM investigation_case
               WHERE case_type = 'deduction' ORDER BY deduction_id, case_id DESC),
         rec AS (SELECT DISTINCT ON (case_id) case_id, payload, calculation_ids, actor_id FROM audit_event
                 WHERE action = 'recommendation' AND actor_type = 'agent' ORDER BY case_id, audit_event_id DESC),
         human AS (SELECT DISTINCT ON (case_id) case_id, action, actor_id, created_at FROM audit_event
                   WHERE actor_type = 'human' AND case_id IS NOT NULL ORDER BY case_id, audit_event_id DESC)
    SELECT c.case_id, c.deduction_id, c.status, c.opened_at, d.claim_number, d.amount, d.deduction_date,
           d.dispute_deadline, d.status AS deduction_status, coalesce(dc.code, '') AS code, d.raw_reason,
           rec.payload->>'action' AS agent_action, rec.payload->>'reason' AS agent_reason,
           rec.payload->>'request' AS agent_request, rec.payload->>'recoverable' AS recoverable,
           rec.payload->>'engine_verdict' AS engine_verdict, rec.payload->>'engine_rule' AS engine_rule,
           rec.actor_id AS agent_version, human.action AS human_action, human.actor_id AS reviewer,
           (SELECT coalesce(max(as_of_date), current_date) FROM world_clock) AS as_of,
           d.dispute_deadline >= (SELECT coalesce(max(as_of_date), current_date) FROM world_clock) AS window_open
    FROM c JOIN rec USING (case_id) JOIN deduction d USING (deduction_id)
    LEFT JOIN deduction_code dc USING (deduction_code_id) LEFT JOIN human USING (case_id)"""


def queue(conn: psycopg.Connection, action: str | None = None, limit: int = 500) -> list[dict]:
    """Latest case per claim. Interesting first: what differs from the engine, then disputes, then the rest."""
    rows = _rows(conn, _LATEST + " WHERE (%s::text IS NULL OR rec.payload->>'action' = %s) ORDER BY c.case_id",
                 (action, action))
    def rank(r):
        differs = not ((r["agent_action"] == "accept" and r["engine_verdict"] == "valid") or
                       (r["agent_action"] == "dispute" and r["engine_verdict"] in ("invalid", "partially_valid")))
        # Decided last; then claims still inside their dispute window (only those can still be recovered);
        # then where the agent and engine differ; disputes before requests before acceptances; nearest deadline.
        return (r["human_action"] is not None, not r["window_open"], not differs,
                {"dispute": 0, "request": 1, "accept": 2}[r["agent_action"]], str(r["dispute_deadline"]))
    for r in rows:
        r["differs_from_engine"] = not ((r["agent_action"] == "accept" and r["engine_verdict"] == "valid") or
                                        (r["agent_action"] == "dispute" and r["engine_verdict"] in ("invalid", "partially_valid")))
    return sorted(rows, key=rank)[:limit]


def detail(conn: psycopg.Connection, case_id: int) -> dict | None:
    head = _rows(conn, _LATEST + " WHERE c.case_id = %s", (case_id,))
    if not head:
        return None
    h = head[0]
    rec = _rows(conn, """SELECT payload, calculation_ids, created_at FROM audit_event WHERE case_id = %s
                         AND action = 'recommendation' ORDER BY audit_event_id DESC LIMIT 1""", (case_id,))[0]
    calcs = _rows(conn, """SELECT calculation_id, kind, formula_version, inputs, result, computed_by, created_at
                           FROM calculation WHERE calculation_id = ANY(%s) ORDER BY calculation_id""",
                  (rec["calculation_ids"] or [],))
    trail = _rows(conn, """SELECT audit_event_id, actor_type, actor_id, action, payload, created_at FROM audit_event
                           WHERE case_id = %s ORDER BY audit_event_id""", (case_id,))
    claim = _rows(conn, """SELECT d.claim_number, d.amount, d.deduction_date, d.dispute_deadline, d.raw_invoice_ref,
                                  d.claimed_units, d.period_start, d.period_end, d.raw_reason, d.store_id
                           FROM observed.deduction d WHERE d.deduction_id = %s""", (h["deduction_id"],))[0]
    names = _rows(conn, """SELECT (SELECT name FROM brand ORDER BY brand_id LIMIT 1) AS supplier,
                                  (SELECT tp.name FROM deduction d JOIN trading_partner tp USING (partner_id)
                                   WHERE d.deduction_id = %s) AS partner""", (h["deduction_id"],))[0]
    finding = rec["payload"]
    return {**h, "finding": finding, "calculations": calcs, "trail": trail, "claim": claim,
            "supplier": names["supplier"] or "Supplier", "partner": names["partner"] or "Partner",
            "letter": _letter(conn, case_id, claim, finding, calcs, names, h["as_of"])}


def _letter(conn, case_id, claim, finding, calcs, names, as_of) -> dict:
    """The model's latest draft written with the current prompt, if every number and date in it is traceable
    today; else the template. A draft from an older prompt is never shown (prompt 1.0 drafts kept the
    engine's wording after 1.1 fixed it)."""
    d = _rows(conn, """SELECT payload, actor_id FROM audit_event WHERE case_id = %s AND action = 'letter_draft'
                       AND prompt_version = %s ORDER BY audit_event_id DESC LIMIT 1""", (case_id, letter.PROMPT_VERSION))
    supplier, partner = names["supplier"] or "Supplier", names["partner"] or "Partner"
    if not d:
        return letter.draft(claim, finding, calcs, supplier, partner, as_of=as_of)
    out = letter.draft(claim, finding, calcs, supplier, partner, as_of=as_of, model=lambda **_: d[0]["payload"]["text"])
    out["model"] = d[0]["payload"].get("model")
    return out


def write_letters(conn, provider, limit: int | None = None, progress=print) -> dict:
    """Ask a model for the letter on every dispute and request case (window open first), check each one, and
    record the draft and its check as an agent audit event. Returns how many passed the number check."""
    todo = [r for r in queue(conn, limit=10_000) if r["agent_action"] in ("dispute", "request") and not r["human_action"]]
    todo = todo[:limit] if limit else todo
    write = letter.model_writer(provider)
    passed, rejected, examples = 0, 0, []
    for i, r in enumerate(todo, 1):
        d = detail(conn, r["case_id"])
        text = write(claim=d["claim"], finding=d["finding"], calculations=d["calculations"],
                     supplier=d["supplier"], partner=d["partner"], as_of=d["as_of"])
        bad = letter.verify(text, d["claim"], d["finding"].get("facts"), str(d["finding"].get("recoverable")),
                            d["calculations"], d["finding"].get("request")) + letter.window_problems(text, d["claim"], d["as_of"])
        passed += not bad
        rejected += bool(bad)
        if bad and len(examples) < 10:
            examples.append({"claim": r["claim_number"], "problems": bad})
        with conn.transaction():
            conn.execute("SET LOCAL ROLE cpgos_agent")
            conn.execute("""INSERT INTO audit_event (case_id, actor_type, actor_id, action, payload, model_version, prompt_version)
                            VALUES (%s, 'agent', %s, 'letter_draft', %s, %s, %s)""",
                         (r["case_id"], letter.LETTER_VERSION, Jsonb({"text": text, "unsupported": bad,
                                                                     "model": f"{provider.name}:{provider.model}"}),
                          f"{provider.name}:{provider.model}", letter.PROMPT_VERSION))
        progress(f"[{i}/{len(todo)}] {r['claim_number']}: {'passed' if not bad else 'rejected ' + ', '.join(bad)}")
    return {"letters": len(todo), "passed_all_checks": passed, "rejected": rejected, "examples": examples,
            "model": f"{provider.name}:{provider.model}", "prompt": letter.PROMPT_VERSION}


def decide(conn: psycopg.Connection, case_id: int, action: str, reviewer: str, note: str | None = None) -> dict:
    """approve (follow the agent), dispute or accept (override), or request_sent (the named document was asked for)."""
    if action not in ("approve", "dispute", "accept", "request_sent"):
        raise CaseError("action must be approve, dispute, accept, or request_sent")
    if not reviewer or not reviewer.strip():
        raise CaseError("a reviewer name is required: every decision is attributed")
    h = detail(conn, case_id)
    if h is None:
        raise CaseError(f"no case {case_id}")
    if h["status"] == "closed":
        raise CaseError("this case is already closed")
    agent = h["agent_action"]
    if action == "request_sent" and agent != "request":
        raise CaseError("the agent did not ask for a document on this case")
    if action == "approve" and agent == "request":
        raise CaseError("the agent asked for a document: mark it as sent, or dispute or accept instead")
    final = agent if action == "approve" else action
    with conn.transaction():
        result = None
        if final in ("dispute", "accept"):
            try:
                result = finance_review.decide_deduction(conn, h["deduction_id"], final, reviewer, note)
            except finance_review.DecisionError as e:
                raise CaseError(str(e)) from None
        conn.execute("SET LOCAL ROLE cpgos_reviewer")
        status = "awaiting_evidence" if final == "request_sent" else "closed"
        conn.execute("UPDATE investigation_case SET status = %s WHERE case_id = %s", (status, case_id))
        conn.execute("""INSERT INTO audit_event (case_id, actor_type, actor_id, action, payload)
                        VALUES (%s, 'human', %s, %s, %s)""",
                     (case_id, reviewer.strip(), f"case_{final}",
                      Jsonb({"agent_recommended": agent, "followed_agent": final == agent or
                             (agent == "request" and final == "request_sent"), "note": note,
                             "document": h["agent_request"] if final == "request_sent" else None})))
    return {"case_id": case_id, "decision": final, "case_status": status,
            "deduction_status": result["status"] if result else h["deduction_status"]}
