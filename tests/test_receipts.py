"""Reading receipt photos: copy-then-classify parsing, arithmetic as a witness, scoring, the rules, the reader loop."""

from __future__ import annotations

import json

import pytest

from cpgos.documents import cord
from cpgos.documents import receipts as R
from cpgos.llm.base import LLMError, LLMResponse, Recorder, parse_json
from tests.test_cord import _gt, _parquet, _png

LABELS = {"version": "test", "labels": {
    "subtotal": ["SUB TOTAL", "SUBTOTAL"], "total": ["GRAND TOTAL", "TOTAL"], "tax": ["PB1", "TAX"],
    "service": ["SERVICE"], "discount": ["DISCOUNT"], "cash": ["CASH", "TUNAI"], "change": ["CHANGE", "KEMBALIAN"],
    "card": ["DEBIT", "GOPAY"]}}

GOOD = {"items": [{"name": "TEH MANIS", "quantity": "2", "unit_price": "5.000", "price": "10.000"},
                  {"name": "NASI GORENG", "quantity": "1", "unit_price": None, "price": "25.000"}],
        "totals": [{"label": "SUBTOTAL", "amount": "35.000"}, {"label": "PB1 10%", "amount": "3.500"},
                   {"label": "TOTAL", "amount": "38.500"}, {"label": "TUNAI", "amount": "50.000"},
                   {"label": "KEMBALIAN", "amount": "-11.500"}]}


def body(**amounts):
    """GOOD with some totals lines changed: body(total="38.900"), or removed: body(subtotal=None)."""
    d = json.loads(json.dumps(GOOD))
    for field, amount in amounts.items():
        lines = [t for t in d["totals"] if R.classify(t["label"], LABELS) == field]
        if amount is None:
            d["totals"] = [t for t in d["totals"] if t not in lines]
        elif lines:
            lines[0]["amount"] = amount
        else:
            d["totals"].append({"label": LABELS["labels"][field][0], "amount": amount})
    return R.parse(d, LABELS)


# ---------- labels decide the field ----------

def test_classify_longest_label_wins_and_one_field_per_line():
    assert R.classify("SUBTOTAL", LABELS) == "subtotal"          # never "TOTAL" inside "SUBTOTAL"
    assert R.classify("Sub Total :", LABELS) == "subtotal"
    assert R.classify("GRAND TOTAL", LABELS) == "total"
    assert R.classify("Total Kembalian", LABELS) == "change"                     # longer label wins: one field
    assert R.classify("PB1 (10%)", LABELS) == "tax"
    assert R.classify("Terima kasih", LABELS) is None             # an unknown label gives no field


def test_a_subtotal_the_receipt_does_not_print_cannot_appear():
    # Report 16: asked for "the subtotal", the model copied the total into it on 33 receipts that print none.
    # Now it can only copy printed lines; with no subtotal line there is no subtotal.
    b = R.parse({"items": GOOD["items"], "totals": [{"label": "TOTAL", "amount": "45,500"},
                                                    {"label": "CASH", "amount": "50,000"},
                                                    {"label": "CHANGE", "amount": "4,500"}]}, LABELS)
    assert b["fields"]["subtotal"] is None and b["fields"]["total"] == "45500"


def test_a_field_printed_twice_with_different_amounts_gives_no_value():
    b = R.parse({"items": [], "totals": [{"label": "TOTAL", "amount": "10.000"}, {"label": "TOTAL", "amount": "12.000"}]}, LABELS)
    assert b["fields"]["total"] is None and b["conflicts"] == ["total"]


def test_parse_reads_rupiah_and_unsigned_change():
    b = body()
    assert b["fields"]["total"] == "38500" and b["fields"]["tax"] == "3500" and b["fields"]["change"] == "11500"
    assert b["items"][0] == {"name": "TEH MANIS", "quantity": "2", "unit_price": "5000", "price": "10000"}


# ---------- arithmetic ----------

def test_a_reading_that_adds_up_passes_and_one_misread_digit_fails():
    assert R.arithmetic(body())[0] is True
    assert R.arithmetic(body(total="38.900"))[0] is False                        # 35,000 + 3,500 is not 38,900
    misread = json.loads(json.dumps(GOOD))
    misread["items"][1]["price"] = "28.000"
    assert R.arithmetic(R.parse(misread, LABELS))[0] is False                    # lines no longer sum to the subtotal


def test_no_total_or_nothing_checkable_never_passes():
    assert R.arithmetic(body(total=None))[0] is False
    assert R.arithmetic(R.parse({"items": [], "totals": [{"label": "TOTAL", "amount": "10.000"}]}, LABELS))[0] is False
    assert R.arithmetic(None)[0] is False


# ---------- regression: bare JSON numbers (report 16) ----------

class Scripted:
    name, model = "fake", "fake-vl"

    def __init__(self, answers):
        self.answers, self.calls = list(answers), 0

    def model_digest(self):
        return "sha256:feedface"

    def complete(self, req):
        self.calls += 1
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        text = a if isinstance(a, str) else json.dumps(a)              # a str is the model's exact text
        return LLMResponse(text, parse_json(text), "fake", self.model, "sha256:feedface", 10, 5, 0.01)


SHRUNK = ('{"items": [{"name": "KOPI", "quantity": 1, "unit_price": null, "price": 81.000}], "totals": ['
          '{"label": "SUBTOTAL", "amount": 81.000}, {"label": "TAX", "amount": 8.100}, {"label": "TOTAL", "amount": 89.100},'
          '{"label": "CASH", "amount": 100.000}, {"label": "CHANGE", "amount": 10.900}]}')


def test_bare_json_numbers_keep_their_printed_digits():
    # The model wrote 89.100 without quotes. As a float it is 89.1, the whole receipt shrinks 1,000 times,
    # and the sums still close, so the check passed wrong readings. Read literally it is 89,100.
    b, _ = R.read(Scripted([SHRUNK]), b"png", "id", LABELS)
    assert b["fields"]["total"] == "89100" and b["fields"]["tax"] == "8100" and b["items"][0]["price"] == "81000"
    assert b["items"][0]["quantity"] == "1"


def test_scale_errors_keep_sums_closed_so_amounts_must_also_look_like_rupiah():
    shrunk = {"fields": {"subtotal": "81", "tax": "8.1", "total": "89.1"}, "items": [{"name": "KOPI", "price": "81"}]}
    assert "fails" not in cord.audit(R.to_receipt(shrunk)).values()               # the sums close...
    ok, why = R.arithmetic(shrunk)
    assert ok is False and "fractional" in why["reason"]                          # ...but rupiah have no fractions
    whole = {"fields": {"subtotal": "81", "tax": "8", "total": "89"}, "items": [{"name": "KOPI", "price": "81"}]}
    assert R.arithmetic(whole) == (False, {"reason": "total under Rp 1,000"})


def test_floats_are_refused_rather_than_silently_converted():
    with pytest.raises(TypeError):
        R.parse({"items": [], "totals": [{"label": "TOTAL", "amount": 89.1}]}, LABELS)


# ---------- scoring ----------

def test_item_matching_tolerates_small_name_slips_but_not_wrong_amounts():
    g = {"name": "NASI GORENG", "quantity": "1", "price": "25000"}
    assert R.item_correct(g, {"name": "NASI G0RENG", "quantity": "1", "price": "25000"})
    assert not R.item_correct(g, {"name": "NASI GORENG", "quantity": "1", "price": "26000"})
    assert not R.item_correct(g, {"name": "ES JERUK", "quantity": "1", "price": "25000"})


def test_score_receipt_counts_each_kind_of_error():
    gold = body()
    assert R.score_receipt(gold, gold)["fully_exact"] == 1
    s = R.score_receipt(gold, body(tax=None, card="38.500"))
    assert (s["missed"], s["spurious"], s["amounts_exact"]) == (1, 1, 0)
    lost = json.loads(json.dumps(GOOD))
    lost["items"] = lost["items"][:1]
    s = R.score_receipt(gold, R.parse(lost, LABELS))
    assert (s["items_correct"], s["items_gold"], s["amounts_exact"]) == (1, 2, 0)
    assert R.score_receipt(gold, None)["missed"] == 5


def test_money_definitions_zero_lines_and_addons():
    gold = body()
    gold["items"][0]["sub_items"] = [{"name": "Top Oreo", "price": "0"}]
    read = json.loads(json.dumps(gold))
    read["items"].append({"name": "Top Oreo", "quantity": None, "unit_price": None, "price": "0"})
    read["fields"]["discount"] = "0"                                              # "Discount 0" printed
    s = R.score_receipt(gold, read)
    assert s["amounts_exact"] == 1 and s["addons_listed_as_lines"] == 1 and s["spurious"] == 0


def test_score_reports_precision_recall_f1():
    gold = {1: body(), 2: body()}
    s = R.score(gold, {1: body()})
    assert s["read"] == 1 and s["amounts_exact"]["k"] == 1
    assert s["totals"]["precision"]["rate"] == 1.0 and s["totals"]["recall"]["rate"] == 0.5


# ---------- the rules ----------

def test_rules():
    good, bad = body(), body(total="38.900")
    assert R.decide("local_alone", good, None)[1] == "local reading adds up"
    assert R.decide("local_alone", bad, good)[0] is None
    assert R.decide("local_then_claude", bad, good) == (good, "Claude's reading adds up")
    assert R.decide("local_then_claude", bad, None)[0] is None
    assert R.decide("local_then_claude_or_agreement", bad, body(total="38.900"))[1].startswith("two readers agree")
    assert R.decide("local_then_claude", bad, body(total="38.900"))[0] is None
    assert R.needs_claude(bad) and not R.needs_claude(good)


def test_test_split_needs_a_frozen_rule():
    assert R.FROZEN_POLICY is None or R.FROZEN_POLICY in R.POLICIES


def test_labels_are_learned_from_train_only_and_each_label_has_one_field():
    def rec(split, cat, label):
        return None, {"meta": {"split": split}, "valid_line": [{"category": cat, "words": [{"text": label, "is_key": 1}]}]}
    recs = [rec("train", "total.total_price", "TOTAL")] * 5 + [rec("train", "sub_total.subtotal_price", "TOTAL")] * 2 + \
           [rec("train", "sub_total.subtotal_price", "SUBTOTAL")] * 3 + [rec("test", "sub_total.tax_price", "SECRET")] * 9
    lab = R.learn_labels(recs)
    assert lab["labels"] == {"subtotal": ["SUBTOTAL"], "total": ["TOTAL"]}      # TOTAL: 5 vs 2, goes to total
    assert lab["labels_claimed_by_several_fields"] == 1                         # test labels never learned


# ---------- the reader loop, end to end on a tiny CORD ----------

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
    assert R.run_reader(conn, tiny, docs, p, "local_model", LABELS, progress=lambda *_: None)["read"] == 2
    again = R.run_reader(conn, tiny, docs, Recorder(Scripted([]), "auto", tmp_path / "cassettes"), "local_model", LABELS,
                         progress=lambda *_: None)
    assert again["to_read"] == 0                                                 # resumable: nothing read twice
    ids = [d[0] for d in docs]
    local, golds = R.readings(conn, R.version(p), ids), R.gold(conn, ids)
    s = R.score(golds, local)
    assert s["fully_exact"]["k"] == 2 and s["items"]["f1"] == 1.0
    pol = R.evaluate_policies(golds, local, {})
    assert pol["local_alone"]["straight_through"]["k"] == 2 and pol["local_alone"]["accepted_amounts_exact"]["k"] == 2


def test_reader_stops_after_three_identical_rejections(conn, tiny, tmp_path):
    err = LLMError("400 bad request")
    err.client_error = True
    p = Recorder(Scripted([err, err, err, GOOD]), "record", tmp_path / "c2")
    with pytest.raises(LLMError, match="identical rejections"):
        R.run_reader(conn, tiny, R.split_documents(conn, "validation") * 2, p, "claude", LABELS, progress=lambda *_: None)
