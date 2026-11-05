"""People reviewing machine decisions on documents (fix 4).

Analogy: a supervisor's sign-off sheet. The machine fills in each field and
marks it "accepted" or "needs review"; a person approves or corrects it. The
sign-off is written in ink: it is never edited (a second opinion is a new
row), every action also lands in the audit log, and a correction is
remembered so the same mistake becomes a regression case.

Who can do what is enforced by database roles, not by this code: the engine
writes decisions, only the reviewer role can write reviews and memory, and
the review and memory tables refuse updates and deletes.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import tuple_row
from psycopg.types.json import Jsonb

from cpgos.documents import normalize as nz


class ReviewError(ValueError):
    pass


def _q(conn: psycopg.Connection, sql: str, params=()):
    """Rows as tuples whatever row factory the caller's connection uses (the API's read connection uses dicts)."""
    return conn.cursor(row_factory=tuple_row).execute(sql, params)


def save_decisions(conn: psycopg.Connection, decisions: dict[int, list], policy_version: str) -> int:
    """Store the routing rule's decisions, as the engine role. Idempotent per document, field, and rule."""
    n = 0
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_engine")
        for sid, ds in decisions.items():
            for d in ds:
                n += _q(conn, 
                    """INSERT INTO field_decision (source_document_id, field_name, policy_version, status, value,
                           reason, candidates) VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (source_document_id, field_name, policy_version) DO NOTHING""",
                    (sid, d.field_name, policy_version, d.status, d.value, d.reason, Jsonb(d.candidates))).rowcount
    return n


def latest_policy(conn: psycopg.Connection, source_document_id: int) -> str | None:
    r = _q(conn, """SELECT policy_version FROM field_decision WHERE source_document_id = %s
                        ORDER BY decided_at DESC, policy_version DESC LIMIT 1""", (source_document_id,)).fetchone()
    return r[0] if r else None


def submit(conn: psycopg.Connection, source_document_id: int, field_name: str, action: str, reviewer: str,
           value: str | None = None, note: str | None = None) -> dict:
    """Approve or correct one field, as the reviewer role. Returns the stored review.

    approve: the machine's value (or its "not on this document") is right.
    correct: the right value is `value` (typed and normalised like the answer key; None = not on the document).
    """
    if action not in ("approve", "correct"):
        raise ReviewError("action must be approve or correct")
    if not reviewer or not reviewer.strip():
        raise ReviewError("a reviewer name is required: every action is attributed")
    if field_name not in nz.FIELD_TYPES:
        raise ReviewError(f"unknown field {field_name!r}")
    reviewed = None
    if action == "correct" and value is not None and str(value).strip():
        reviewed = nz.normalize_field(field_name, str(value))
        if reviewed is None:
            raise ReviewError(f"{value!r} is not a valid {nz.FIELD_TYPES[field_name]} for {field_name}")
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_reviewer")
        policy = latest_policy(conn, source_document_id)
        if policy is None:
            raise ReviewError(f"document {source_document_id} has no machine decisions to review")
        dec = _q(conn, """SELECT status, value, candidates FROM field_decision
                              WHERE source_document_id = %s AND field_name = %s AND policy_version = %s""",
                           (source_document_id, field_name, policy)).fetchone()
        if dec is None:
            raise ReviewError(f"no decision for {field_name} on document {source_document_id}")
        status, machine_value, candidates = dec
        if action == "approve":
            reviewed = machine_value
        rid, created = _q(conn, 
            """INSERT INTO field_review (source_document_id, field_name, policy_version, action, machine_status,
                   machine_value, reviewed_value, reviewer, note)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING field_review_id, created_at""",
            (source_document_id, field_name, policy, action, status, machine_value, reviewed, reviewer.strip(),
             note)).fetchone()
        changed = action == "correct" and reviewed != machine_value
        if changed:
            _q(conn, """INSERT INTO mapping_memory (field_review_id, source_document_id, field_name, machine_value,
                                candidates, correct_value) VALUES (%s, %s, %s, %s, %s, %s)""",
                         (rid, source_document_id, field_name, machine_value, Jsonb(candidates), reviewed))
        _q(conn, """INSERT INTO audit_event (actor_type, actor_id, action, payload)
                        VALUES ('human', %s, %s, %s)""",
                     (reviewer.strip(), f"field_{action}",
                      Jsonb({"source_document_id": source_document_id, "field": field_name, "policy": policy,
                             "machine_status": status, "machine_value": machine_value, "reviewed_value": reviewed,
                             "field_review_id": rid, "remembered": changed})))
    return {"field_review_id": rid, "source_document_id": source_document_id, "field_name": field_name,
            "action": action, "machine_value": machine_value, "reviewed_value": reviewed,
            "remembered": changed, "created_at": created.isoformat()}


def document_view(conn: psycopg.Connection, source_document_id: int) -> dict | None:
    """What the reviewer sees: each field's machine decision, the readers' values, a box on the page
    when a reader located the value, and the latest human review. Never reads eval.* (no answer key)."""
    doc = _q(conn, "SELECT source_document_id, storage_key, source_type, mime_type FROM source_document "
                       "WHERE source_document_id = %s", (source_document_id,)).fetchone()
    if doc is None:
        return None
    policy = latest_policy(conn, source_document_id)
    fields = []
    if policy:
        for f, status, value, reason, cands in _q(conn, 
                """SELECT field_name, status, value, reason, candidates FROM field_decision
                   WHERE source_document_id = %s AND policy_version = %s ORDER BY field_name""",
                (source_document_id, policy)):
            box = _q(conn, """SELECT bbox FROM field_extraction WHERE source_document_id = %s AND field_name = %s
                                  AND bbox IS NOT NULL AND (value = %s::text OR %s::text IS NULL) ORDER BY extractor_version LIMIT 1""",
                               (source_document_id, f, value, value)).fetchone()
            rev = _q(conn, """SELECT action, reviewed_value, reviewer, created_at FROM field_review
                                  WHERE source_document_id = %s AND field_name = %s AND policy_version = %s
                                  ORDER BY field_review_id DESC LIMIT 1""", (source_document_id, f, policy)).fetchone()
            fields.append({"field": f, "kind": nz.FIELD_TYPES.get(f), "status": status, "value": value,
                           "reason": reason, "candidates": cands, "bbox": box[0] if box else None,
                           "review": None if rev is None else {"action": rev[0], "value": rev[1], "reviewer": rev[2],
                                                                "at": rev[3].isoformat()}})
    open_ = sum(1 for x in fields if x["status"] == "review" and x["review"] is None)
    return {"source_document_id": doc[0], "storage_key": doc[1], "source_type": doc[2], "mime_type": doc[3],
            "policy_version": policy, "fields": fields, "open_fields": open_, "readings": readings(conn, source_document_id)}


def reader_of(extractor_version: str) -> str | None:
    if extractor_version.startswith("rules-"):
        return "rules"
    if "+anthropic:" in extractor_version:
        return "claude"
    if "+ollama:" in extractor_version:
        return "local_model"
    return None


def readings(conn: psycopg.Connection, source_document_id: int) -> list[dict]:
    """What each reader read, with where on the page (for the replay). Latest version of each reader."""
    out, seen = [], set()
    for ver, f, value, bbox in _q(conn, """SELECT extractor_version, field_name, value, bbox FROM field_extraction
                                           WHERE source_document_id = %s ORDER BY extractor_version DESC, field_name""",
                                  (source_document_id,)):
        who = reader_of(ver)
        if who is None or (who, f) in seen:
            continue
        seen.add((who, f))
        out.append({"reader": who, "field": f, "value": value, "bbox": bbox})
    order = {"rules": 0, "local_model": 1, "claude": 2}
    return sorted(out, key=lambda r: (order[r["reader"]], r["field"]))


def queue(conn: psycopg.Connection, limit: int = 50) -> list[dict]:
    """Documents with fields still waiting for a person, most waiting fields first."""
    rows = _q(conn, 
        """WITH latest AS (
               SELECT DISTINCT ON (source_document_id) source_document_id, policy_version FROM field_decision
               ORDER BY source_document_id, decided_at DESC, policy_version DESC)
           SELECT d.source_document_id, l.policy_version,
                  count(*) FILTER (WHERE d.status = 'review' AND NOT EXISTS (
                      SELECT 1 FROM field_review r WHERE r.source_document_id = d.source_document_id
                      AND r.field_name = d.field_name AND r.policy_version = d.policy_version)) AS open_fields,
                  count(*) FILTER (WHERE d.status = 'accepted') AS accepted_fields,
                  array_agg(DISTINCT d.field_name) FILTER (WHERE d.status = 'review') AS review_fields
           FROM field_decision d JOIN latest l USING (source_document_id, policy_version)
           GROUP BY d.source_document_id, l.policy_version
           HAVING count(*) FILTER (WHERE d.status = 'review' AND NOT EXISTS (
                      SELECT 1 FROM field_review r WHERE r.source_document_id = d.source_document_id
                      AND r.field_name = d.field_name AND r.policy_version = d.policy_version)) > 0
           ORDER BY open_fields DESC, d.source_document_id LIMIT %s""", (limit,)).fetchall()
    return [{"source_document_id": r[0], "policy_version": r[1], "open_fields": r[2], "accepted_fields": r[3],
             "review_fields": sorted(r[4] or [])} for r in rows]
