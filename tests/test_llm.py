import json

import httpx
import pytest

from cpgos.documents import vlm
from cpgos.llm import base
from cpgos.llm.anthropic_provider import Anthropic
from cpgos.llm.base import LLMError, LLMRequest, LLMResponse, Recorder, ReplayMiss
from cpgos.llm.ollama import Ollama


class Fake:
    """A provider that answers from a script and counts calls."""
    name = "fake"

    def __init__(self, text='{"total": "TOTAL : 734.33 EUR"}', digest="sha256:abc"):
        self.model, self.text, self.digest, self.calls = "fake-1", text, digest, 0

    def model_digest(self):
        return self.digest

    def complete(self, req):
        self.calls += 1
        return LLMResponse(self.text, base.parse_json(self.text), "fake", self.model, self.digest, 10, 5, 0.01)


REQ = LLMRequest(system="s", prompt="read", images=[b"img-1"], json_schema={"type": "object"})


def test_record_then_replay_without_calling(tmp_path):
    f = Fake()
    r1 = Recorder(f, "auto", tmp_path).complete(REQ)
    assert f.calls == 1 and not r1.replayed and r1.data == {"total": "TOTAL : 734.33 EUR"}
    r2 = Recorder(f, "auto", tmp_path).complete(REQ)
    assert f.calls == 1 and r2.replayed and r2.text == r1.text


def test_replay_mode_never_calls_and_misses_loudly(tmp_path):
    f = Fake()
    with pytest.raises(ReplayMiss, match="no recording"):
        Recorder(f, "replay", tmp_path).complete(REQ)
    assert f.calls == 0
    Recorder(f, "auto", tmp_path).complete(REQ)
    # Replay works even when the provider is unreachable (no digest available), via the index.
    f.digest = None
    assert Recorder(f, "replay", tmp_path).complete(REQ).replayed


def test_key_changes_with_anything_that_changes_the_answer():
    k = base.request_key("p", "m", "d1", REQ)
    assert k != base.request_key("p", "m", "d2", REQ)                                   # re-pulled model
    assert k != base.request_key("p", "m", "d1", LLMRequest("s", "read", [b"img-2"], {"type": "object"}))
    assert k != base.request_key("p", "m", "d1", LLMRequest("s", "read!", [b"img-1"], {"type": "object"}))
    assert k == base.request_key("p", "m", "d1", LLMRequest("s", "read", [b"img-1"], {"type": "object"}))


def test_record_mode_overwrites(tmp_path):
    f = Fake()
    Recorder(f, "auto", tmp_path).complete(REQ)
    f.text = '{"total": "1.00"}'
    assert Recorder(f, "record", tmp_path).complete(REQ).data == {"total": "1.00"}
    assert Recorder(f, "auto", tmp_path).complete(REQ).data == {"total": "1.00"}


@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Here you go: {"a": 1} hope it helps', {"a": 1}),
    ("not json", None),
    ("[1, 2]", None),
])
def test_parse_json(text, expected):
    assert base.parse_json(text) == expected


def test_unknown_mode_is_refused(tmp_path):
    with pytest.raises(ValueError, match="CPGOS_LLM_MODE"):
        Recorder(Fake(), "sometimes", tmp_path)


# ---------- providers (HTTP mocked) ----------

def test_ollama_sends_schema_and_image_and_reads_digest(monkeypatch):
    sent = {}

    def fake_get(url, timeout):
        return httpx.Response(200, json={"models": [{"name": "qwen2.5vl:7b", "digest": "sha256:feed"}]},
                              request=httpx.Request("GET", url))

    def fake_post(url, json, timeout):
        sent.update(json)
        return httpx.Response(200, json={"message": {"content": '{"total": "9.99"}'}, "prompt_eval_count": 900,
                                         "eval_count": 30}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    r = Ollama("qwen2.5vl:7b", "http://x").complete(REQ)
    assert r.data == {"total": "9.99"} and r.model_digest == "sha256:feed" and r.input_tokens == 900
    assert sent["format"] == {"type": "object"} and sent["options"]["temperature"] == 0
    assert sent["messages"][-1]["images"] == [base.b64(b"img-1")]


def test_ollama_down_says_how_to_start(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(httpx, "get", boom)
    with pytest.raises(LLMError, match="ollama serve"):
        Ollama("m", "http://x").model_digest()


def test_ollama_missing_model_says_how_to_pull(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, timeout: httpx.Response(
        200, json={"models": []}, request=httpx.Request("GET", url)))
    with pytest.raises(LLMError, match="ollama pull qwen"):
        Ollama("qwen2.5vl:7b", "http://x").model_digest()


SECRET = "sk-ant-api03-THIS-IS-A-TEST-SECRET"


def test_anthropic_key_is_sent_only_in_the_header_and_never_recorded(monkeypatch, tmp_path):
    seen = {}

    def fake_post(url, json, timeout, headers):
        seen.update(headers=headers, body=json)
        return httpx.Response(200, json={"model": "claude-sonnet-5-5", "content": [{"type": "text", "text": '{"total": "1.00"}'}],
                                         "usage": {"input_tokens": 1500, "output_tokens": 20}}, request=httpx.Request("POST", url))

    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.setattr(httpx, "post", fake_post)
    r = Recorder(Anthropic(), "auto", tmp_path).complete(REQ)
    assert r.data == {"total": "1.00"} and r.input_tokens == 1500
    assert seen["headers"]["x-api-key"] == SECRET and SECRET not in json.dumps(seen["body"])
    # Regression: claude-sonnet-5-5 rejects "temperature" (400 on every call).
    assert "temperature" not in seen["body"]
    for f in tmp_path.rglob("*"):
        if f.is_file():
            assert SECRET not in f.read_text()


def test_anthropic_without_key_fails_without_echoing_anything(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("cpgos.config.get", lambda k, d=None: None)
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY is not set"):
        Anthropic().complete(REQ)


def test_anthropic_network_error_does_not_leak_the_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)

    def boom(*a, **k):
        raise httpx.ConnectError(f"failed with header {SECRET}")
    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(LLMError) as e:
        Anthropic().complete(REQ)
    assert SECRET not in str(e.value) and e.value.__cause__ is None


# ---------- vision extraction: typing the model's printed values ----------

def test_model_values_are_typed_by_the_shared_normaliser(tmp_path):
    answer = {"invoice_number": "INV/30-14/832", "invoice_date": "20-Mar-2008", "due_date": None,
              "po_number": None, "seller_name": "Fernandez PLC", "buyer_name": "Denise Perez",
              "subtotal": "725.30 EUR", "discount": "(-) 13.42", "tax": "63.18 + 63.18", "total": "TOTAL : 734.33 EUR",
              "amount_due": "null", "currency": "EUR",
              "total_in_words": "seven hundred and thirty-four point three three"}
    exs, meta = vlm.extract(Recorder(Fake(json.dumps(answer)), "auto", tmp_path), b"png")
    got = {e.field_name: e.value for e in exs}
    assert got["invoice_date"] == "2008-03-20" and got["total"] == "734.33" and got["tax"] == "126.36"
    assert got["discount"] == "13.42" and got["currency"] == "EUR" and got["total_in_words"] == "734.33"
    assert got["amount_due"] is None and got["due_date"] is None
    assert got["seller_name"] == "Fernandez PLC" and meta["parsed_json"] and meta["prompt_version"] == vlm.PROMPT_VERSION
    assert {e.path for e in exs} == {"vlm:fake"}


def test_non_json_answer_is_an_error_not_a_guess(tmp_path):
    exs, meta = vlm.extract(Recorder(Fake("I think the total is 734"), "auto", tmp_path), b"png")
    assert exs == [] and "JSON" in meta["error"]


def test_schema_asks_for_every_field_and_nothing_else():
    assert set(vlm.SCHEMA["required"]) == set(vlm.FIELD_HELP) == set(vlm.nz.FIELD_TYPES)
    assert vlm.SCHEMA["additionalProperties"] is False


# ---------- grounding: a model value counts only if it is on the page, in the right role ----------

from tests.test_rules_extraction import words_of  # noqa: E402

LEX = {"subtotal": [("SUB_TOTAL", 20)], "total": [("TOTAL", 20)], "buyer_name": [("Bill to", 21)],
       "invoice_number": [("INVOICE #", 29)]}
PAGE = words_of(("INVOICE # INV/30-14/832", 0.55, 0.05, "1.1.1"),
                ("Address:16424 Timothy Mission", 0.05, 0.20, "2.1.1"),
                ("Bill to:Denise Perez", 0.55, 0.20, "3.1.1"),
                ("Total in words: seven hundred and thirt-", 0.03, 0.70, "4.1.1"),
                ("SUB_TOTAL : 725.30 EUR", 0.60, 0.705, "5.1.1"),
                ("y-four point three three", 0.03, 0.716, "4.1.2"),
                ("TOTAL : 734.33 EUR", 0.60, 0.85, "5.1.2"))


def _ex(field, printed):
    from cpgos.documents.rules import Extraction
    return Extraction(field, vlm._typed(field, printed), printed, None, "vlm:fake", {"printed": printed})


@pytest.mark.parametrize("field,printed,kept,why", [
    ("total", "734.33 EUR", True, "amount on page"),
    ("total", "430.57", False, "amount not on page"),          # regression: the model computed a total
    ("subtotal", "725.30", True, "amount on page"),             # regression: "TOTAL" inside "SUB_TOTAL" was a false role conflict
    ("subtotal", "734.33", False, "labelled total"),            # regression: total copied into subtotal
    ("buyer_name", "Denise Perez", True, "text match"),
    # Regression (grounding 2.0): the seller's address was accepted as the buyer because nothing
    # contradicted it; a labelled field now needs its own label next to the value.
    ("buyer_name", "Timothy Mission", False, "no label for this field"),
    ("invoice_number", "INV/30-14/832", True, "id on page"),
    ("invoice_number", "INV/99-99/999", False, "id not on page"),
    ("currency", "EUR", True, "currency on page"),
    ("currency", "USD", False, "currency not on page"),
    ("total_in_words", "seven hundred and thirty-four point three three", True, "letters found"),   # wraps lines and columns
])
def test_grounding(field, printed, kept, why):
    out = vlm.grounded([_ex(field, printed)], PAGE, LEX)[0]
    assert (out.value is not None) == kept, out.signals
    assert why in out.signals["grounding"]
    if kept:
        assert out.bbox is not None


def test_image_identity_keys_survive_different_encoders():
    # Regression: keys hashed encoded PNG bytes; another machine's encoder wrote different bytes for
    # the same pixels, so recordings made on the Mac never replayed in the sandbox or CI.
    a = LLMRequest("s", "read", [b"png-bytes-from-mac"], {"type": "object"}, image_ids=["abc@png-x1.0"])
    b = LLMRequest("s", "read", [b"png-bytes-from-linux"], {"type": "object"}, image_ids=["abc@png-x1.0"])
    assert base.request_key("p", "m", "d", a) == base.request_key("p", "m", "d", b)
    c = LLMRequest("s", "read", [b"png-bytes-from-mac"], {"type": "object"}, image_ids=["abc@png-x2.0"])
    assert base.request_key("p", "m", "d", a) != base.request_key("p", "m", "d", c)


def test_model_sums_are_refused():
    # Regression (calibration): the model summed line items into a subtotal ("107.32 + 128.30 + 22.80");
    # the last number was on the page, so the sum passed as a "grounded" subtotal.
    out = vlm.grounded([_ex("subtotal", "107.32 + 128.30 + 725.30")], PAGE, LEX)[0]
    assert out.value is None and "sum" in out.signals["grounding"]


def test_unlabelled_number_is_not_an_invoice_number():
    # Regression (calibration): a bare number elsewhere on the page was accepted as the invoice number.
    page = PAGE + words_of(("030063", 0.05, 0.95, "9.1.1"))
    out = vlm.grounded([_ex("invoice_number", "030063")], page, LEX)[0]
    assert out.value is None and "no label" in out.signals["grounding"]


@pytest.mark.parametrize("printed", ["8", "66636 Hernandez Loaf Suite 714"])
def test_implausible_names_are_refused(printed):
    out = vlm.grounded([_ex("seller_name", printed)], PAGE + words_of((printed, 0.05, 0.6, "8.1.1")), LEX)[0]
    assert out.value is None and "plausible" in out.signals["grounding"]


def test_label_with_an_ocr_slip_still_counts_as_the_fields_own():
    # The case the model is for: OCR mangled the label, so tier 1 could not read the field.
    page = PAGE + words_of(("T0TAL : 512.00 EUR", 0.60, 0.92, "6.1.1"))
    out = vlm.grounded([_ex("total", "512.00")], page, LEX)[0]
    assert out.value == "512.00"
