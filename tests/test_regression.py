"""Values a person confirmed become tests the routing rule must keep passing."""

from __future__ import annotations

import json

import pytest

from cpgos.documents import regression as rg
from cpgos.documents import review, routing

INPUTS = {"rules": {"total": "734.33", "tax": "28.18", "invoice_number": "INV-7"},
          "local_model": {"total": "734.33", "tax": "28.16", "invoice_number": "INV-7"},
          "claude": None, "printed_labels": ["tax"], "seen": {}, "unverifiable": []}


def _doc(conn, key="fatura/x/Template1_Instance1.jpg", sha="a"):
    sid = conn.execute("""INSERT INTO source_document (sha256, storage_key, source_type, mime_type)
                          VALUES (%s, %s, 'invoice', 'image/jpeg') RETURNING source_document_id""",
                       (sha * 64, key)).fetchone()[0]
    assert review.save_decisions(conn, {sid: routing.decide_from(INPUTS)}, routing.POLICY_VERSION, {sid: INPUTS}) > 0
    return sid


@pytest.fixture
def reviewed(conn):
    sid = _doc(conn)
    review.submit(conn, sid, "total", "approve", "akila")                 # accepted 734.33, confirmed right
    review.submit(conn, sid, "tax", "correct", "akila", value="28.18")    # readers disagreed; person says 28.18
    review.submit(conn, sid, "invoice_number", "correct", "akila", value="INV-1")   # machine accepted INV-7: wrong
    return sid


def test_the_current_rule_passes_and_names_its_known_mistake(conn, reviewed):
    out = rg.summary(rg.check(rg.cases(conn)))
    assert (out["confirmed_values"], out["kept_right"], out["asks_a_person"]) == (3, 1, 1)
    assert out["open_mistakes"] == 1 and out["regressions"] == 0 and out["passed"] is True
    assert out["wrong"] == [{"doc": reviewed, "field": "invoice_number", "truth": "INV-1", "now_accepts": "INV-7",
                             "kind": "open_mistake", "reviewed_under": routing.POLICY_VERSION}]


def test_a_rule_change_that_accepts_a_value_a_person_rejected_fails_the_gate(conn, reviewed):
    def trusting(inputs):                       # "when readers disagree, trust the local model"
        ds = routing.decide_from(inputs)
        return [routing.Decision(d.field_name, d.candidates.get("local_model"), "accepted", "trust local", d.candidates)
                if d.status == "review" and "local_model" in d.candidates else d for d in ds]
    out = rg.summary(rg.check(rg.cases(conn), decide=trusting))
    assert out["passed"] is False and out["regressions"] == 1
    assert [(w["field"], w["truth"], w["now_accepts"], w["kind"]) for w in out["wrong"] if w["kind"] == "regression"] == \
        [("tax", "28.18", "28.16", "regression")]


def test_a_fix_turns_the_open_mistake_into_a_kept_value(conn, reviewed):
    def fixed(inputs):
        return [routing.Decision(d.field_name, "INV-1", "accepted", "fixed", d.candidates)
                if d.field_name == "invoice_number" else d for d in routing.decide_from(inputs)]
    out = rg.summary(rg.check(rg.cases(conn), decide=fixed))
    assert out["open_mistakes"] == 0 and out["corrections_now_accepted_right"] == 1


def test_only_the_latest_review_counts_and_unreplayable_fields_are_reported(conn, reviewed):
    review.submit(conn, reviewed, "tax", "correct", "akila", value="28.16")     # a second opinion replaces the first
    assert {c.field: c.truth for c in rg.cases(conn)}["tax"] == "28.16"
    old = conn.execute("""INSERT INTO source_document (sha256, storage_key, source_type) VALUES (%s, 'fatura/x/old.jpg',
                          'invoice') RETURNING source_document_id""", ("c" * 64,)).fetchone()[0]
    review.save_decisions(conn, {old: routing.decide_from(INPUTS)}, routing.POLICY_VERSION)   # no inputs stored
    review.submit(conn, old, "total", "approve", "akila")
    assert rg.without_inputs(conn) == 1 and old not in {c.source_document_id for c in rg.cases(conn)}


def test_export_keeps_redistributable_invoices_only_and_round_trips(conn, reviewed, tmp_path):
    other = _doc(conn, key="docile/annotated/x.pdf", sha="d")
    review.submit(conn, other, "total", "approve", "akila")
    path = tmp_path / "r.json"
    n = rg.export(rg.cases(conn, exportable_only=True), path)
    assert n == 3 and "docile" not in path.read_text()
    back = rg.load(path)
    assert {c.source_document_id for c in back} == {reviewed}
    assert rg.summary(rg.check(back))["passed"] is True
    assert json.loads(path.read_text())["about"].startswith("Values a person confirmed")


def test_engine_stores_inputs_reviewer_cannot(conn, reviewed):
    import psycopg
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.transaction():
            conn.execute("SET LOCAL ROLE cpgos_reviewer")
            conn.execute("INSERT INTO routing_input (source_document_id, policy_version, inputs) VALUES (%s, 'x', '{}')",
                         (reviewed,))


def test_api_reports_the_gate(db_url, conn, reviewed, monkeypatch):
    from fastapi.testclient import TestClient
    from cpgos.api import app as api
    monkeypatch.setenv("CPGOS_DATABASE_URL", db_url)
    r = TestClient(api.app).get("/api/documents/regression").json()
    assert r["passed"] is True and r["confirmed_values"] == 3 and r["open_mistakes"] == 1 and r["not_replayable_yet"] == 0
