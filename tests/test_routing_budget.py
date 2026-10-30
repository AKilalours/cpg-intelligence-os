from decimal import Decimal

import httpx
import pytest

from cpgos.documents import routing
from cpgos.llm import budget
from cpgos.llm.anthropic_provider import Anthropic
from cpgos.llm.base import LLMRequest
from cpgos.llm.budget import BudgetExceeded


# ---------- the two-readers rule ----------

def _status(ds):
    return {d.field_name: (d.status, d.value, d.reason) for d in ds}


def test_agreement_is_accepted_and_disagreement_is_not():
    s = _status(routing.decide({"total": "734.33", "tax": "28.18"}, {"total": "734.33", "tax": "28.16"}))
    assert s["total"] == ("accepted", "734.33", "two readers agree")
    assert s["tax"][0] == "review" and s["tax"][2] == "readers disagree"


def test_a_lone_reader_is_never_enough():
    s = _status(routing.decide({}, {"seller_name": "Fernandez PLC"}))
    assert s["seller_name"][0] == "review" and s["seller_name"][2] == "only one reader found it"


def test_third_reader_breaks_ties_and_confirms_lone_readers():
    s = _status(routing.decide({"tax": "28.18"}, {"tax": "28.16", "seller_name": "Fernandez PLC"},
                               {"tax": "28.18", "seller_name": "Fernandez PLC"}))
    assert s["tax"] == ("accepted", "28.18", "third reader agrees with one of two")
    assert s["seller_name"] == ("accepted", "Fernandez PLC", "third reader agrees with one of two")


def test_third_reader_alone_does_not_decide():
    s = _status(routing.decide({}, {}, {"po_number": "35"}))
    assert s["po_number"][0] == "review"


def test_optional_absent_is_accepted_but_required_absent_is_not():
    s = _status(routing.decide({}, {}))
    assert s["po_number"] == ("accepted", None, "no reader found it; optional field")
    assert s["total"][0] == "review" and s["total"][2] == "required field not found"


def test_only_unsettled_fields_need_a_third_reader():
    r = {"invoice_number": "1", "invoice_date": "2008-03-20", "total": "1.00"}
    assert routing.needs_third_reader(r, dict(r)) == []
    assert routing.needs_third_reader(r, {**r, "total": "7.00"}) == ["total"]
    assert routing.needs_third_reader(r, {**r, "seller_name": "X"}) == ["seller_name"]


def test_summary_counts_wrong_acceptances_against_the_key():
    ds = {1: routing.decide({"invoice_number": "A", "invoice_date": "2008-03-20", "total": "1.00"},
                            {"invoice_number": "A", "invoice_date": "2008-03-20", "total": "1.00"})}
    s = routing.summarize(ds, {1: {"invoice_number": "A", "invoice_date": "2008-03-20", "total": "1.00", "po_number": "35"}})
    assert s["straight_through"]["k"] == 1
    assert s["accepted_by_reason"]["wrong: no reader found it; optional field"] == 1     # a missed PO number


# ---------- the spending cap ----------

def test_cost_uses_published_prices():
    assert budget.cost("claude-sonnet-5-5", 1_000_000, 0) == Decimal("2")
    assert budget.cost("claude-sonnet-5-5", 0, 1_000_000) == Decimal("10")
    assert budget.cost("claude-sonnet-5-5", 1400, 300) == Decimal("0.0058")


def test_unknown_model_price_blocks_paid_calls(monkeypatch):
    monkeypatch.setattr("cpgos.config.get", lambda k, d=None: None)
    with pytest.raises(BudgetExceeded, match="no price known"):
        budget.price("some-new-model")


def test_cap_refuses_before_sending(tmp_path, monkeypatch):
    ledger = tmp_path / "spend.jsonl"
    monkeypatch.setattr("cpgos.config.get", lambda k, d=None: "0.01" if k == "CPGOS_LLM_BUDGET_USD" else None)
    budget.record("claude-sonnet-5-5", 2000, 500, ledger=ledger)                 # $0.009 spent
    assert budget.spent(ledger) == Decimal("0.009")
    with pytest.raises(BudgetExceeded, match="cap"):
        budget.check("claude-sonnet-5-5", 1400, 700, ledger)                     # worst case $0.0098 > $0.001 left


def test_claude_call_is_capped_and_logged(tmp_path, monkeypatch):
    ledger = tmp_path / "spend.jsonl"
    sent = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("CPGOS_LLM_BUDGET_USD", "0.015")

    def fake_post(url, json, timeout, headers):
        sent.append(1)
        return httpx.Response(200, json={"model": "claude-sonnet-5-5", "content": [{"type": "text", "text": "{}"}],
                                         "usage": {"input_tokens": 1500, "output_tokens": 300}}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", fake_post)
    p = Anthropic(ledger=ledger)
    req = LLMRequest("s", "read", [], {"type": "object"}, max_tokens=700)
    p.complete(req)                                                               # $0.006
    p.complete(req)                                                               # $0.012
    assert budget.spent(ledger) == Decimal("0.012") and len(sent) == 2
    with pytest.raises(BudgetExceeded):
        p.complete(req)                                                           # $0.003 left < $0.007 worst case
    assert len(sent) == 2                                                         # refused before sending


def test_identical_rejections_stop_the_run_early(conn, monkeypatch):
    # Regression: 120 calls were sent into the same 400 error before the run ended.
    from cpgos.documents import cascade, rules, vlm
    conn.execute("INSERT INTO source_document (sha256, storage_key, source_type, mime_type) "
                 "SELECT md5(i::text) || md5(i::text), 'x' || i, 'invoice', 'image/jpeg' FROM generate_series(1, 10) i")
    conn.execute("INSERT INTO rules_model (version, content, trained_on, content_sha256) "
                 "VALUES ('r', '{\"regions\": {}, \"lexicon\": {}}', '{}', repeat('c', 64))")
    ids = [r[0] for r in conn.execute("SELECT source_document_id FROM source_document")]
    monkeypatch.setattr(cascade.ocr, "ocr_image", lambda path, sha: [])
    monkeypatch.setattr(vlm, "page_png", lambda path, scale=1.0: b"png")
    calls = []
    monkeypatch.setattr(vlm, "extract", lambda p, img, iid=None: (calls.append(1), ([], {
        "error": "Anthropic returned 400: temperature is deprecated", "client_error": True}))[1])

    class P:
        name, model = "anthropic", "claude-sonnet-5-5"
        def digest(self):
            return None
    out = cascade.run_tier2(conn, __import__("pathlib").Path("."), ids, P(), progress=lambda m: None, extractor="claude")
    assert len(calls) == cascade.MAX_SAME_ERRORS and out["stopped_early"] == 1
