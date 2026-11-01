"""Human review of machine decisions: attribution, append-only history, remembered corrections, roles, API."""

from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient

from cpgos.api import app as api
from cpgos.documents import review, routing
from cpgos.documents.routing import Decision


@pytest.fixture
def doc(conn):
    sid = conn.execute("""INSERT INTO source_document (sha256, storage_key, source_type, mime_type)
                          VALUES (%s, 'fatura/x/Template1_Instance1.jpg', 'invoice', 'image/jpeg')
                          RETURNING source_document_id""", ("a" * 64,)).fetchone()[0]
    ds = [Decision("total", "734.33", "accepted", "two readers agree", {"rules": "734.33", "local_model": "734.33"}),
          Decision("tax", None, "review", "readers disagree", {"rules": "28.18", "local_model": "28.16"}),
          Decision("invoice_number", None, "review", "required field not found", {})]
    assert review.save_decisions(conn, {sid: ds}, routing.POLICY_VERSION) == 3
    assert review.save_decisions(conn, {sid: ds}, routing.POLICY_VERSION) == 0           # idempotent
    return sid


def test_approve_is_attributed_and_audited_but_not_remembered(conn, doc):
    r = review.submit(conn, doc, "total", "approve", "akila")
    assert r["reviewed_value"] == "734.33" and r["remembered"] is False
    a = conn.execute("SELECT actor_type, actor_id, action, payload->>'field' FROM audit_event").fetchall()
    assert a == [("human", "akila", "field_approve", "total")]
    assert conn.execute("SELECT count(*) FROM mapping_memory").fetchone()[0] == 0


def test_a_correction_is_normalised_and_remembered_with_what_the_readers_said(conn, doc):
    r = review.submit(conn, doc, "tax", "correct", "akila", value="28.18 EUR", note="checked the page")
    assert r["reviewed_value"] == "28.18" and r["remembered"] is True
    m = conn.execute("SELECT field_name, machine_value, candidates, correct_value FROM mapping_memory").fetchone()
    assert m == ("tax", None, {"rules": "28.18", "local_model": "28.16"}, "28.18")


def test_correcting_to_not_on_the_document_is_allowed(conn, doc):
    r = review.submit(conn, doc, "invoice_number", "correct", "akila", value=None, note="layout prints none")
    assert r["reviewed_value"] is None and r["remembered"] is False      # same as the machine: nothing to learn


def test_bad_input_writes_nothing(conn, doc):
    for kwargs in ({"action": "delete"}, {"action": "correct", "value": "not money"}, {"action": "approve", "reviewer": " "}):
        with pytest.raises(review.ReviewError):
            review.submit(conn, doc, "total", kwargs.get("action"), kwargs.get("reviewer", "akila"), kwargs.get("value"))
    with pytest.raises(review.ReviewError):
        review.submit(conn, doc, "no_such_field", "approve", "akila")
    assert conn.execute("SELECT count(*) FROM field_review").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM audit_event").fetchone()[0] == 0


def test_reviews_and_memory_are_append_only(conn, doc):
    review.submit(conn, doc, "tax", "correct", "akila", value="28.18")
    for sql in ("UPDATE field_review SET reviewer = 'someone else'", "DELETE FROM field_review",
                "UPDATE mapping_memory SET correct_value = '0'", "DELETE FROM mapping_memory"):
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute(sql)


def test_roles_engine_decides_only_reviewer_reviews_only(conn, doc):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.transaction():
            conn.execute("SET LOCAL ROLE cpgos_engine")
            conn.execute("""INSERT INTO field_review (source_document_id, field_name, policy_version, action,
                            machine_status, reviewer) VALUES (%s, 'total', %s, 'approve', 'accepted', 'engine')""",
                         (doc, routing.POLICY_VERSION))
    for sql in ("UPDATE field_decision SET status = 'accepted'", "DELETE FROM field_decision"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("SET LOCAL ROLE cpgos_reviewer")
                conn.execute(sql)


def test_queue_lists_only_documents_with_open_fields(conn, doc):
    q = review.queue(conn)
    assert [(x["source_document_id"], x["open_fields"]) for x in q] == [(doc, 2)]
    review.submit(conn, doc, "tax", "correct", "akila", value="28.18")
    review.submit(conn, doc, "invoice_number", "approve", "akila")
    assert review.queue(conn) == []
    v = review.document_view(conn, doc)
    assert v["open_fields"] == 0 and {f["field"]: f["review"]["action"] for f in v["fields"] if f["review"]} == \
        {"tax": "correct", "invoice_number": "approve"}


# ---------- API ----------

@pytest.fixture
def client(db_url, conn, monkeypatch, tmp_path):
    monkeypatch.setenv("CPGOS_DATABASE_URL", db_url)
    monkeypatch.setattr(api, "RAW_ROOT", tmp_path)
    return TestClient(api.app)


def test_api_review_round_trip(client, conn, doc, tmp_path):
    assert client.get("/api/documents/queue").json()[0]["open_fields"] == 2
    r = client.post(f"/api/documents/{doc}/fields/tax/review", json={"action": "correct", "value": "28.18", "reviewer": "akila"})
    assert r.status_code == 200 and r.json()["remembered"] is True
    bad = client.post(f"/api/documents/{doc}/fields/tax/review", json={"action": "correct", "value": "abc", "reviewer": "akila"})
    assert bad.status_code == 400
    d = client.get(f"/api/documents/{doc}").json()
    assert d["open_fields"] == 1 and d["image_url"].endswith("/image")
    assert client.get(f"/api/documents/{doc}/image").status_code == 404               # file not on this machine
    img = tmp_path / "fatura/x/Template1_Instance1.jpg"
    img.parent.mkdir(parents=True)
    img.write_bytes(b"\xff\xd8\xff")
    assert client.get(f"/api/documents/{doc}/image").status_code == 200


def test_api_image_never_leaves_the_data_folder(client, conn):
    sid = conn.execute("""INSERT INTO source_document (sha256, storage_key, source_type)
                          VALUES (%s, '../../../etc/passwd', 'other') RETURNING source_document_id""", ("b" * 64,)).fetchone()[0]
    assert client.get(f"/api/documents/{sid}/image").status_code == 404
