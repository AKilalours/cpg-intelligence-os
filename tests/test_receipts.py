"""Reading receipt photos: parsing model output, arithmetic as a witness, scoring, the rules, the reader loop."""

from __future__ import annotations

import json

import pytest

from cpgos.documents import cord
from cpgos.documents import receipts as R
from cpgos.llm.base import LLMError, LLMResponse, Recorder, parse_json
from tests.test_cord import _gt, _parquet, _png

GOOD = {"items": [{"name": "TEH MANIS", "quantity": "2", "unit_price": "5.000", "price": "10.000"},
                  {"name": "NASI GORENG", "quantity": "1", "unit_price": None, "price": "25.000"}],
        "subtotal": "35.000", "discount": None, "tax": "3.500", "service": None, "total": "38.500",
        "cash": "50.000", "change": "-11.500", "card": None}


def body(**changes):
    d = json.loads(json.dumps(GOOD))
    d.update(changes)
    return R.parse(d)


# ---------- parsing and arithmetic ----------

def test_parse_reads_rupiah_strings_numbers_and_unsigned_change():
    b = R.parse({**GOOD, "total": 38500, "subtotal": "Rp 35,000"})
    assert b["fields"]["total"] == "38500" and b["fields"]["subtotal"] == "35000"
    assert b["fields"]["change"] == "11500"                      # printed with a minus sign; compared as an amount
    assert b["items"][0] == {"name": "TEH MANIS", "quantity": "2", "unit_price": "5000", "price": "10000"}


def test_a_reading_that_adds_up_passes_and_one_misread_digit_fails():
    assert R.arithmetic(body())[0] is True
    assert R.arithmetic(body(total="38.900"))[0] is False                       # 35,000 + 3,500 is not 38,900
    misread_line = json.loads(json.dumps(GOOD))
    misread_line["items"][1]["price"] = "28.000"
    assert R.arithmetic(R.parse(misread_line))[0] is False                       # lines no longer sum to the subtotal


def test_no_total_or_nothing_checkable_never_passes():
    assert R.arithmetic(body(total=None))[0] is False
    assert R.arithmetic(R.parse({"items": [], "total": "10.000"}))[0] is False   # nothing to check it against
    assert R.arithmetic(None)[0] is False


# ---------- scoring ----------

def test_item_matching_tolerates_small_name_slips_but_not_wrong_amounts():
    g = {"name": "NASI GORENG", "quantity": "1", "price": "25000"}
    assert R.item_correct(g, {"name": "NASI G0RENG", "quantity": "1", "price": "25000"})
    assert not R.item_correct(g, {"name": "NASI GORENG", "quantity": "1", "price": "26000"})
    assert not R.item_correct(g, {"name": "ES JERUK", "quantity": "1", "price": "25000"})


def test_score_receipt_counts_each_kind_of_error():
    gold = R.parse(GOOD)
    assert R.score_receipt(gold, gold)["fully_exact"] == 1
    s = R.score_receipt(gold, body(tax=None, card="38.500"))
    assert (s["missed"], s["spurious"], s["amounts_exact"]) == (1, 1, 0)
    lost_line = json.loads(json.dumps(GOOD))
    lost_line["items"] = lost_line["items"][:1]
    s = R.score_receipt(gold, R.parse(lost_line))
    assert (s["items_correct"], s["items_gold"], s["amounts_exact"]) == (1, 2, 0)
    assert R.score_receipt(gold, None)["missed"] == 5


def test_score_reports_precision_recall_f1():
    gold = {1: R.parse(GOOD), 2: R.parse(GOOD)}
    s = R.score(gold, {1: R.parse(GOOD)})
    assert s["read"] == 1 and s["amounts_exact"]["k"] == 1
    assert s["totals"]["precision"]["rate"] == 1.0 and s["totals"]["recall"]["rate"] == 0.5


# ---------- the rules ----------

def test_rules():
    good, bad = body(), body(total="38.900")
    assert R.decide("local_alone", good, None)[1] == "local reading adds up"
    assert R.decide("local_alone", bad, good)[0] is None                         # never asks a second reader
    assert R.decide("local_then_claude", bad, good) == (good, "Claude's reading adds up")
    assert R.decide("local_then_claude", bad, None)[0] is None
    # Two readers that agree on the total and every line amount, though neither adds up:
    assert R.decide("local_then_claude_or_agreement", bad, body(total="38.900"))[1].startswith("two readers agree")
    assert R.decide("local_then_claude", bad, body(total="38.900"))[0] is None
    assert R.needs_claude(bad) and not R.needs_claude(good)


def test_test_split_needs_a_frozen_rule():
    assert R.FROZEN_POLICY is None or R.FROZEN_POLICY in R.POLICIES


# ---------- the reader loop, end to end on a tiny CORD ----------

class Scripted:
    name, model = "fake", "fake-vl"

    def __init__(self, answers):
        self.answers, self.calls = list(answers), 0

    def model_digest(self):
        return "sha256:feedface"

    def complete(self, req):
        self.calls += 1
        a = self.answers.pop(0) if self.answers else self.answers_last
        if isinstance(a, Exception):
            raise a
        text = json.dumps(a)
        return LLMResponse(text, parse_json(text), "fake", self.model, "sha256:feedface", 10, 5, 0.01)


@pytest.fixture
def tiny(conn, tmp_path):
    gt = _gt([{"nm": "TEH MANIS", "cnt": "2", "unitprice": "5.000", "price": "10.000"},
              {"nm": "NASI GORENG", "cnt": "1", "price": "25.000"}],
             {"subtotal_price": "35.000", "tax_price": "3.500"},
             {"total_price": "38.500", "cashprice": "50.000", "changeprice": "11.500"}, split="valid", image_id=1)
    _parquet(tmp_path / "cord" / "data", [(_png((9, 9, 9)), gt), (_png((8, 8, 8)), {**gt, "meta": {**gt["meta"], "image_id": 2}})])
    cord.load_cord(conn, tmp_path)
    return tmp_path


def test_reader_stores_as_engine_resumes_and_scores_perfectly(conn, tiny, tmp_path):
    docs = R.split_documents(conn, "validation")
    p = Recorder(Scripted([GOOD, GOOD]), "auto", tmp_path / "cassettes")
    rep = R.run_reader(conn, tiny, docs, p, "local_model", progress=lambda *_: None)
    assert rep["read"] == 2
    again = R.run_reader(conn, tiny, docs, Recorder(Scripted([]), "auto", tmp_path / "cassettes"), "local_model",
                         progress=lambda *_: None)
    assert again["to_read"] == 0                                                 # resumable: nothing read twice
    ids = [d[0] for d in docs]
    local = R.readings(conn, R.version(p), ids)
    golds = R.gold(conn, ids)
    s = R.score(golds, local)
    assert s["fully_exact"]["k"] == 2 and s["items"]["f1"] == 1.0
    pol = R.evaluate_policies(golds, local, {})
    assert pol["local_alone"]["straight_through"]["k"] == 2 and pol["local_alone"]["accepted_amounts_exact"]["k"] == 2


def test_reader_stops_after_three_identical_rejections(conn, tiny, tmp_path):
    err = LLMError("400 bad request")
    err.client_error = True
    p = Recorder(Scripted([err, err, err, GOOD]), "record", tmp_path / "c2")
    with pytest.raises(LLMError, match="identical rejections"):
        R.run_reader(conn, tiny, R.split_documents(conn, "validation") * 2, p, "claude", progress=lambda *_: None)
