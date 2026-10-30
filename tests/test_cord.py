"""CORD receipts: rupiah parsing, answer-key shapes, the label audit, and the loader."""

from __future__ import annotations

import io
import json
from decimal import Decimal

import psycopg
import pytest
from PIL import Image

from cpgos.documents import cord

# ---------- amounts ----------


@pytest.mark.parametrize("raw, want", [
    ("60.000", "60000"),          # Indonesian thousands separator: sixty thousand, not sixty
    ("5.455", "5455"),
    ("1,944,000", "1944000"),
    ("45,000.00", "45000"),
    ("9.500,00", "9500"),
    ("17500", "17500"),
    ("-3,200", "-3200"),
    ("@20,000", "20000"),
    ("Rp. 12.000", "12000"),
    ("[25,000]", "25000"),        # box printed around a payment
    ("39,800)", "-39800"),        # clipped accounting minus
    ("TOTAL 47,499", "47499"),
    ("*46000", "46000"),
    ("40,000.", "40000"),
    ("60.00", "60"),
])
def test_parse_idr(raw, want):
    assert cord.canonical(cord.parse_idr(raw)) == want


@pytest.mark.parametrize("raw", ["Rp 57,0000", "39,200,00", "121.000.00", "-", "", None, "abc", "1.2.3"])
def test_parse_idr_refuses_what_it_cannot_read_for_certain(raw):
    assert cord.parse_idr(raw) is None


def test_invoice_parser_would_have_read_rupiah_wrong():
    # The reason receipts get their own parser.
    from cpgos.documents import normalize as nz
    assert nz.parse_amount("60.000") != Decimal("60000")
    assert cord.parse_idr("60.000") == Decimal("60000")


@pytest.mark.parametrize("raw, want", [("2", "2"), ("x1", "1"), ("1X", "1"), ("2.00", "2"), ("0,5", "0.5"),
                                       ("1.00xITEMs", "1"), ("(Qty=4.00)", "4"), ("ITEMS :4", "4"), ("6 Item(s)", "6")])
def test_parse_count(raw, want):
    assert cord.canonical(cord.parse_count(raw)) == want


def test_parse_count_needs_exactly_one_number():
    assert cord.parse_count("1 of 2") is None and cord.parse_count("-") is None


# ---------- answer key ----------

def _gt(menu, sub_total=None, total=None, split="train", image_id=0, lines=()):
    p = {"menu": menu}
    if sub_total is not None:
        p["sub_total"] = sub_total
    if total is not None:
        p["total"] = total
    return {"gt_parse": p, "meta": {"split": split, "image_id": image_id, "image_size": {"width": 100, "height": 200}},
            "valid_line": list(lines)}


def test_single_item_and_list_of_items_both_load():
    one = cord.gold_from_record(_gt({"nm": "TEH", "cnt": "1", "price": "5.000"}))
    many = cord.gold_from_record(_gt([{"nm": "TEH", "price": "5.000"}, {"nm": "NASI", "price": "10.000"}]))
    assert [i.price for i in one.items] == [Decimal(5000)]
    assert [i.name for i in many.items] == ["TEH", "NASI"]


def test_split_names_and_doc_keys():
    r = cord.gold_from_record(_gt({"nm": "A", "price": "1"}, split="valid", image_id=7))
    assert (r.split, r.doc_key) == ("validation", "validation/0007")
    with pytest.raises(cord.CordFormatError):
        cord.gold_from_record({"gt_parse": {}, "meta": {"split": "other", "image_id": 1}})


def test_field_labelled_twice_is_dropped_not_guessed():
    r = cord.gold_from_record(_gt({"nm": "A", "price": "1"}, sub_total=[{"tax_price": "1"}, {"tax_price": "2"}]))
    assert "tax" not in r.fields and r.notes["tax: labelled more than once"] == 1


def test_discount_sign_is_normalised_and_boxes_come_from_word_quads():
    line = {"category": "total.total_price", "group_id": 3,
            "words": [{"quad": {"x1": 10, "y1": 20, "x2": 50, "y2": 20, "x3": 50, "y3": 40, "x4": 10, "y4": 40}}]}
    r = cord.gold_from_record(_gt({"nm": "A", "price": "10"}, sub_total={"discount_price": "-60.000"},
                                  total={"total_price": "60.000"}, lines=[line]))
    assert r.value("discount") == Decimal(60000)
    assert r.fields["total"][2] == [0.1, 0.1, 0.5, 0.2]


# ---------- audit ----------

def test_audit_exact_rounded_and_failing():
    ok = cord.gold_from_record(_gt([{"nm": "A", "cnt": "2", "unitprice": "10,000", "price": "20,000"}],
                                   {"subtotal_price": "20,000", "tax_price": "2,000"},
                                   {"total_price": "22,000", "cashprice": "50,000", "changeprice": "28,000"}))
    assert cord.audit(ok) == {"items_sum": "exact", "subtotal_to_total": "exact", "change": "exact", "line_amount": "exact"}
    rounded = cord.gold_from_record(_gt([{"nm": "A", "price": "87,275"}], {"subtotal_price": "87,275", "tax_price": "8,728"},
                                        {"total_price": "96,000"}))
    assert cord.audit(rounded)["subtotal_to_total"] == "rounded"           # 96,003 printed as 96,000
    bad = cord.gold_from_record(_gt([{"nm": "-TICKET CP", "price": "60.000"}],
                                    {"subtotal_price": "60.000", "discount_price": "-60.000", "tax_price": "5.455"},
                                    {"total_price": "60.000"}))
    assert cord.audit(bad)["subtotal_to_total"] == "fails"                  # the published label does not add up


def test_audit_understands_tax_inclusive_prices_and_printed_minus_on_change():
    r = cord.gold_from_record(_gt([{"nm": "MINI CHOCO", "price": "13,500"}], {"subtotal_price": "12,273", "tax_price": "1,227"},
                                  {"total_price": "13,500", "cashprice": "14,000", "changeprice": "-500"}))
    a = cord.audit(r)
    assert a["items_sum"] == "exact" and a["change"] == "exact"


def test_audit_not_applicable_when_values_missing():
    r = cord.gold_from_record(_gt({"nm": "A"}))
    assert set(cord.audit(r).values()) == {None}


# ---------- loader ----------

def _png(color):
    b = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(b, "PNG")
    return b.getvalue()


def _parquet(dirpath, rows):
    import duckdb
    dirpath.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("CREATE TABLE t (image STRUCT(bytes BLOB, path VARCHAR), ground_truth VARCHAR)")
    for img, gt in rows:
        con.execute("INSERT INTO t VALUES ({'bytes': ?, 'path': NULL}, ?)", [img, json.dumps(gt)])
    con.execute(f"COPY t TO '{dirpath / 'train-00000.parquet'}' (FORMAT parquet)")


@pytest.fixture
def raw(tmp_path):
    rows = [(_png((i * 40, 0, 0)), _gt([{"nm": f"ITEM {i}", "cnt": "1", "price": "10.000"}], {"subtotal_price": "10.000"},
                                         {"total_price": "10.000"}, split=sp, image_id=i))
            for i, sp in enumerate(["train", "train", "valid", "test"])]
    _parquet(tmp_path / "cord" / "data", rows)
    return tmp_path


def test_load_writes_images_gold_and_is_idempotent(conn, raw):
    rep, receipts = cord.load_cord(conn, raw)
    assert rep.receipts == 4 and rep.documents_new == 4 and rep.images_written == 4
    assert rep.by_split == {"train": 2, "validation": 1, "test": 1}
    assert (raw / "cord" / "images" / "validation" / "0002.png").is_file()
    assert conn.execute("SELECT count(*) FROM eval.receipt_item_gold").fetchone()[0] == 4
    assert conn.execute("SELECT value FROM eval.receipt_gold WHERE field_name = 'total' LIMIT 1").fetchone()[0] == "10000"
    assert conn.execute("SELECT DISTINCT source_type FROM source_document").fetchall() == [("receipt",)]
    rep2, _ = cord.load_cord(conn, raw)
    assert rep2.documents_new == 0 and rep2.images_written == 0
    assert conn.execute("SELECT count(*) FROM eval.receipt_meta").fetchone()[0] == 4
    assert conn.execute("SELECT count(*) FROM eval.receipt_gold").fetchone()[0] == rep.fields


def test_engine_cannot_read_the_receipt_answer_key(conn, raw):
    cord.load_cord(conn, raw)
    for t in ("eval.receipt_gold", "eval.receipt_item_gold", "eval.receipt_meta"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("SET LOCAL ROLE cpgos_engine")
                conn.execute(f"SELECT 1 FROM {t}")


def test_same_image_twice_in_one_split_keeps_the_first_and_reports_the_copy(conn, tmp_path):
    # CORD v2 really has two such pairs (train 0058/0109 and 0265/0285), labelled twice by different hands.
    img = _png((1, 2, 3))
    _parquet(tmp_path / "cord" / "data", [(img, _gt({"nm": "B"}, image_id=9)), (img, _gt({"nm": "A"}, image_id=4))])
    rep, receipts = cord.load_cord(conn, tmp_path)
    assert rep.duplicates_dropped == [{"dropped": "train/0009", "kept": "train/0004"}]
    assert [r.doc_key for r in receipts] == ["train/0004"]
    assert conn.execute("SELECT doc_key FROM eval.receipt_meta").fetchall() == [("train/0004",)]
    assert not (tmp_path / "cord" / "images" / "train" / "0009.png").exists()


def test_same_image_in_two_splits_is_refused_before_anything_is_written(conn, tmp_path):
    img = _png((1, 2, 3))
    _parquet(tmp_path / "cord" / "data", [(img, _gt({"nm": "A"}, image_id=0)), (img, _gt({"nm": "B"}, split="test", image_id=1))])
    with pytest.raises(cord.CordFormatError, match="different splits"):
        cord.load_cord(conn, tmp_path)
    assert conn.execute("SELECT count(*) FROM source_document").fetchone()[0] == 0
    assert not (tmp_path / "cord" / "images").exists()
