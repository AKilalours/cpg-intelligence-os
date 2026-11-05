"""Every value a person confirmed becomes a test the routing rule must keep passing (fix 4).

Analogy: a binder of mistakes we promised never to repeat. When a reviewer approves or corrects a
field, the true value of that field on that invoice is known for good. Each later rule version is
replayed on the exact inputs the rule saw for that invoice (stored in routing_input, so no model is
called) and must not ACCEPT anything else. Sending the field to a person is allowed: it is slower,
but never wrong. Two kinds of wrong are kept apart:
  regression    the rule now accepts a wrong value it did not accept when the person reviewed it.
                This fails the gate.
  open_mistake  the rule still accepts the very value the person corrected. A known bug: reported
                and counted, but it does not fail the gate, or the gate would stay red until every
                old mistake is fixed and people would learn to ignore it.

The cases can be exported to a JSON file that the ordinary test suite replays without a database,
so a rule change that breaks a confirmed value fails in CI before it reaches anyone. Only FATURA
invoices are exported: other document sets (DocILE) may not be redistributed.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import psycopg
from psycopg.rows import tuple_row

from cpgos.documents import routing

EXPORTABLE_PREFIXES = ("fatura/",)       # redistributable sets only; DocILE never leaves this machine


@dataclass(frozen=True)
class Case:
    source_document_id: int
    field: str
    truth: str | None                   # canonical; None = not on this document
    action: str                         # approve | correct
    reviewed_under: str                 # the rule version the person saw
    machine_value: str | None
    machine_status: str                 # what the rule did when the person reviewed: accepted | review
    inputs: dict


@dataclass(frozen=True)
class Outcome:
    case: Case
    status: str                          # what the current rule does: accepted | review
    value: str | None
    verdict: str                         # kept_right | asks_a_person | open_mistake | regression


def cases(conn: psycopg.Connection, exportable_only: bool = False) -> list[Case]:
    """The latest human review of each field, with the newest stored inputs for its invoice."""
    rows = conn.cursor(row_factory=tuple_row).execute(
        """WITH latest AS (
               SELECT DISTINCT ON (source_document_id, field_name) source_document_id, field_name, action,
                      reviewed_value, machine_value, machine_status, policy_version
               FROM field_review ORDER BY source_document_id, field_name, field_review_id DESC),
           inp AS (
               SELECT DISTINCT ON (source_document_id) source_document_id, inputs FROM routing_input
               ORDER BY source_document_id, created_at DESC, policy_version DESC)
           SELECT l.source_document_id, l.field_name, l.reviewed_value, l.action, l.policy_version, l.machine_value,
                  l.machine_status, i.inputs, d.storage_key
           FROM latest l JOIN inp i USING (source_document_id) JOIN source_document d USING (source_document_id)
           ORDER BY l.source_document_id, l.field_name""").fetchall()
    return [Case(sid, f, truth, action, pol, mv, ms, inputs) for sid, f, truth, action, pol, mv, ms, inputs, key in rows
            if not exportable_only or key.startswith(EXPORTABLE_PREFIXES)]


def without_inputs(conn: psycopg.Connection) -> int:
    """Reviewed fields that cannot be replayed yet (decided before inputs were stored: rerun make decide-docs)."""
    return conn.cursor(row_factory=tuple_row).execute("""SELECT count(DISTINCT (source_document_id, field_name)) FROM field_review r
                           WHERE NOT EXISTS (SELECT 1 FROM routing_input i
                                             WHERE i.source_document_id = r.source_document_id)""").fetchone()[0]


def check(cs: list[Case], decide: Callable[[dict], list] = routing.decide_from) -> list[Outcome]:
    out = []
    by_doc: dict[int, dict[str, routing.Decision]] = {}
    for c in cs:
        if c.source_document_id not in by_doc:
            by_doc[c.source_document_id] = {d.field_name: d for d in decide(c.inputs)}
        d = by_doc[c.source_document_id].get(c.field)
        if d is None or d.status == "review":
            out.append(Outcome(c, "review", None, "asks_a_person"))
        elif d.value == c.truth:
            out.append(Outcome(c, "accepted", d.value, "kept_right"))
        elif c.machine_status == "accepted" and d.value == c.machine_value:
            out.append(Outcome(c, "accepted", d.value, "open_mistake"))
        else:
            out.append(Outcome(c, "accepted", d.value, "regression"))
    return out


def summary(outcomes: list[Outcome], policy: str = routing.POLICY_VERSION) -> dict:
    v = Counter(o.verdict for o in outcomes)
    corrected = [o for o in outcomes if o.case.action == "correct"]
    return {"policy": policy, "confirmed_values": len(outcomes),
            "kept_right": v["kept_right"], "asks_a_person": v["asks_a_person"],
            "open_mistakes": v["open_mistake"], "regressions": v["regression"],
            "corrections": len(corrected),
            "corrections_now_accepted_right": sum(o.verdict == "kept_right" for o in corrected),
            "passed": v["regression"] == 0,
            "wrong": [{"doc": o.case.source_document_id, "field": o.case.field, "truth": o.case.truth,
                       "now_accepts": o.value, "kind": o.verdict, "reviewed_under": o.case.reviewed_under}
                      for o in outcomes if o.verdict in ("regression", "open_mistake")][:50]}


def export(cs: list[Case], path: Path) -> int:
    """Write cases a test can replay without a database. Callers pass exportable cases only; checked here too."""
    bad = [c for c in cs if not isinstance(c.inputs, dict)]
    if bad:
        raise ValueError("cases without inputs cannot be exported")
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"about": "Values a person confirmed on FATURA invoices. The routing rule must never accept "
                     "anything else on these inputs. Regenerate with: make export-regressions",
            "cases": [asdict(c) for c in cs]}
    path.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n")
    return len(cs)


def load(path: Path) -> list[Case]:
    if not path.exists():
        return []
    return [Case(**c) for c in json.loads(path.read_text())["cases"]]
