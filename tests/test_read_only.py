"""The hosted demo is public: CPGOS_READ_ONLY=1 must refuse every write before it reaches a handler."""

from fastapi.testclient import TestClient

from cpgos.api import app as api

WRITES = [
    "/api/documents/1/fields/total/review",
    "/api/cash/1/review",
    "/api/deductions/1/review",
    "/api/cases/1/decision",
    "/api/drift/1/clear",
]


def test_every_write_route_is_refused_in_read_only_mode(monkeypatch):
    monkeypatch.setenv("CPGOS_READ_ONLY", "1")
    c = TestClient(api.app)
    # Every POST route the app has must be covered: a new write route without a test here fails this line.
    posts = {r.path for r in api.app.routes if "POST" in getattr(r, "methods", set())}
    assert len(posts) == len(WRITES), sorted(posts)
    for path in WRITES:
        r = c.post(path, json={"reviewer": "x", "decision": "approve"})
        assert r.status_code == 403, (path, r.status_code)
        assert "read-only" in r.json()["detail"]
    assert c.get("/api/mode").json() == {"read_only": True}


def test_writes_reach_handlers_when_not_read_only(monkeypatch):
    monkeypatch.delenv("CPGOS_READ_ONLY", raising=False)
    monkeypatch.setattr(api.config, "get", lambda k, d=None: d)
    c = TestClient(api.app)
    assert c.get("/api/mode").json() == {"read_only": False}
    # Not refused by the gate: it gets as far as validation or the database (no database in this test).
    r = c.post("/api/cash/1/review", json={})
    assert r.status_code != 403 and "read-only" not in r.text
