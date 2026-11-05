"""Reviewing the agent's cases: queue order, the full case, decisions, and the API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cpgos.agent import cases
from cpgos.agent import investigate as inv
from cpgos.api import app as api
from tests.test_agent_investigate import _world


@pytest.fixture
def investigated(conn):
    _world(conn, damage={"p_promo_missing": 0.5, "p_receipt_missing": 0.3}, p_promo_overclaim=0.5)
    inv.save(conn, inv.investigate(conn))
    return conn


def _first(conn, action):
    return next(r for r in cases.queue(conn) if r["agent_action"] == action)


def test_the_queue_puts_what_needs_a_person_first(investigated):
    q = cases.queue(investigated)
    assert q and len({r["deduction_id"] for r in q}) == len(q)            # one case per claim
    # Claims still inside their dispute window come first: only those can still be recovered.
    windows = [r["window_open"] for r in q]
    assert windows == sorted(windows, reverse=True)
    for w in (True, False):
        order = [r["agent_action"] for r in q if r["window_open"] is w and not r["differs_from_engine"]]
        assert order == sorted(order, key={"dispute": 0, "request": 1, "accept": 2}.get)


def test_a_case_shows_the_finding_its_arithmetic_trail_and_a_checked_letter(investigated):
    d = cases.detail(investigated, _first(investigated, "dispute")["case_id"])
    assert d["finding"]["action"] == "dispute" and d["calculations"] and d["trail"][0]["actor_type"] == "agent"
    assert d["letter"]["unsupported"] == [] and d["claim"]["claim_number"] in d["letter"]["text"]
    assert d["finding"]["tool_calls"]


def test_approving_a_dispute_disputes_the_claim_and_closes_the_case(investigated):
    c = _first(investigated, "dispute")
    r = cases.decide(investigated, c["case_id"], "approve", "akila")
    assert r == {"case_id": c["case_id"], "decision": "dispute", "case_status": "closed", "deduction_status": "disputed"}
    ev = investigated.execute("""SELECT actor_type, action, payload->>'followed_agent' FROM audit_event
                                 WHERE case_id = %s ORDER BY audit_event_id""", (c["case_id"],)).fetchall()
    assert ev[-1] == ("human", "case_dispute", "true")
    with pytest.raises(cases.CaseError, match="already closed"):
        cases.decide(investigated, c["case_id"], "accept", "akila")


def test_overriding_the_agent_is_recorded_as_such(investigated):
    c = _first(investigated, "accept")
    r = cases.decide(investigated, c["case_id"], "dispute", "akila", note="I know this one")
    assert r["deduction_status"] == "disputed"
    assert investigated.execute("SELECT payload->>'followed_agent' FROM audit_event WHERE case_id = %s AND actor_type = 'human'",
                                (c["case_id"],)).fetchone()[0] == "false"


def test_a_request_is_marked_sent_and_the_claim_stays_open(investigated):
    c = _first(investigated, "request")
    with pytest.raises(cases.CaseError, match="asked for a document"):
        cases.decide(investigated, c["case_id"], "approve", "akila")
    r = cases.decide(investigated, c["case_id"], "request_sent", "akila")
    assert r["case_status"] == "awaiting_evidence" and r["deduction_status"] in ("open", "investigating")


def test_bad_input_changes_nothing(investigated):
    c = _first(investigated, "dispute")
    for args in (("delete", "akila"), ("approve", " "), ("request_sent", "akila")):
        with pytest.raises(cases.CaseError):
            cases.decide(investigated, c["case_id"], *args)
    assert investigated.execute("SELECT count(*) FROM audit_event WHERE actor_type = 'human'").fetchone()[0] == 0


def test_api_round_trip(db_url, investigated, monkeypatch):
    monkeypatch.setenv("CPGOS_DATABASE_URL", db_url)
    client = TestClient(api.app)
    q = client.get("/api/cases").json()
    assert q and client.get("/api/cases", params={"action": "dispute"}).json()[0]["agent_action"] == "dispute"
    cid = next(r["case_id"] for r in q if r["agent_action"] == "dispute")
    d = client.get(f"/api/cases/{cid}").json()
    # Money arrives as exact decimal strings, never floats (a float crashed the case screen).
    assert all(isinstance(r["amount"], str) and "." in r["amount"] for r in q)
    assert isinstance(d["amount"], str) and all(isinstance(k["result"], str) for k in d["calculations"])
    assert d["letter"]["text"] and d["calculations"]
    assert client.post(f"/api/cases/{cid}/decision", json={"action": "approve", "reviewer": "akila"}).status_code == 200
    again = client.post(f"/api/cases/{cid}/decision", json={"action": "approve", "reviewer": "akila"})
    assert again.status_code == 400 and "closed" in again.json()["detail"]
    assert client.get("/api/cases/999999").status_code == 404


class FakeModel:
    """A model that writes a sensible letter, but invents a number on every third case."""
    name, model = "fake", "letters-v1"

    def __init__(self):
        self.n = 0

    def complete(self, req):
        from types import SimpleNamespace
        import json
        self.n += 1
        facts = json.loads(req.prompt.split("Facts (JSON):\n", 1)[1])
        text = f"Dear {facts['to']}, about claim {facts['claim']} ({facts['claim_amount']}): {facts['decision']}."
        if self.n % 3 == 0:
            text += " Please also pay a $99.99 handling fee within 30 days."
        return SimpleNamespace(text=text)


def test_model_letters_are_used_only_when_every_number_traces(investigated):
    out = cases.write_letters(investigated, FakeModel(), progress=lambda m: None)
    assert out["letters"] > 0 and out["rejected"] == out["letters"] // 3 and out["passed_number_check"] == out["letters"] - out["rejected"]
    assert all(set(e["invented"]) == {"30", "99.99"} for e in out["examples"])
    q = [r for r in cases.queue(investigated) if r["agent_action"] in ("dispute", "request")]
    sources = {cases.detail(investigated, r["case_id"])["letter"]["source"] for r in q}
    assert sources == {"model", "template"}                    # good drafts used, invented ones fell back
    who = investigated.execute("SELECT DISTINCT actor_type, model_version FROM audit_event WHERE action = 'letter_draft'").fetchall()
    assert who == [("agent", "fake:letters-v1")]



def test_a_draft_from_an_older_prompt_is_never_shown(investigated, monkeypatch):
    from cpgos.agent import letter
    cases.write_letters(investigated, FakeModel(), progress=lambda m: None)
    monkeypatch.setattr(letter, "PROMPT_VERSION", "letter-prompt-9.9.9")
    q = [r for r in cases.queue(investigated) if r["agent_action"] in ("dispute", "request")]
    assert {cases.detail(investigated, r["case_id"])["letter"]["source"] for r in q} == {"template"}
