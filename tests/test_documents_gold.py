import json
from datetime import date
from decimal import Decimal

import psycopg
import pytest

from cpgos.documents import fatura as F
from cpgos.documents import normalize as nz


# ---------- normalisation: the same code turns gold and predictions into typed values ----------

@pytest.mark.parametrize("text,expected", [
    ("Invoice Date: 04-May-2007", date(2007, 5, 4)),
    ("Due Date : 01-Feb-2000", date(2000, 2, 1)),
    ("Date: 03-Jan-1994", date(1994, 1, 3)),
    ("2008-03-20", date(2008, 3, 20)),
    ("March 20, 2008", date(2008, 3, 20)),
    ("20.03.2008", date(2008, 3, 20)),      # day > 12 proves day first
    ("03/20/2008", date(2008, 3, 20)),      # day > 12 proves month first
    ("03/04/2008", None),                   # ambiguous: refuse rather than guess
    ("31-Feb-2008", None),                  # impossible date
    ("", None),
])
def test_parse_date(text, expected):
    assert nz.parse_date(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("TOTAL : 441.14 EUR", Decimal("441.14")),
    ("SUB_TOTAL : 1,057.19  USD", Decimal("1057.19")),
    ("TAX:VAT (3.85%):  10.26 $", Decimal("10.26")),     # the rate is not the amount
    ("DISCOUNT(2.14%): (-)  9.39", Decimal("9.39")),
    ("GST(18%) : 244.91", Decimal("244.91")),
    ("Total 1.234,56 EUR", Decimal("1234.56")),           # European separators
    ("BALANCE_DUE : 481.84 $", Decimal("481.84")),
    ("no amount here", None),
])
def test_parse_amount(text, expected):
    assert nz.parse_amount(text, last=True) == expected


def test_amount_is_exact_never_float():
    assert isinstance(nz.parse_amount("TOTAL : 0.30 $", last=True), Decimal)


def test_two_amounts_without_last_is_refused():
    assert nz.parse_amount("12.00 and 13.00") is None


@pytest.mark.parametrize("text,expected", [
    ("TOTAL : 441.14 EUR", "EUR"), ("TOTAL : 264.25 $", "USD"), ("TOTAL : 998.28 USD", "USD"), ("TOTAL : 1", None),
])
def test_parse_currency(text, expected):
    assert nz.parse_currency(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("INVOICE # INV/30-14/832", "INV/30-14/832"),
    ("Invoice number 2970-559", "2970-559"),
    ("INVOICE ID INV38499489", "INV38499489"),
    ("PO Number :35", "35"),
    ("PO Number :01", "01"),                 # leading zero is part of the identifier
])
def test_normalize_id(text, expected):
    assert nz.normalize_id(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("Bill to:Denise Perez\n16424 Timothy Mission", "Denise Perez"),
    ("Buyer :Alexander Williams\n6479 Smith Causeway", "Alexander Williams"),
    ("BILL_TO:\nChad Pearson\n504 Mikayla Roads", "Chad Pearson"),
])
def test_normalize_name(text, expected):
    assert nz.normalize_name(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("Total in words:  seven hundred and thirt-\ny-four point three three", Decimal("734.33")),
    ("Total in words:  two hundred and forty-\n-eight point nine four", Decimal("248.94")),
    # Regression: a line-break hyphen between two words was glued into "hundredand"
    # and 629 of 4,000 FATURA values failed to parse.
    ("Total in words:  one thousand, one hundred-\nand ten point seven seven", Decimal("1110.77")),
    ("Total in words:  two hundred and nin-\nety-seven point one", Decimal("297.10")),
    ("Total in words: banana", None),
])
def test_words_to_amount(text, expected):
    assert nz.words_to_amount(text) == expected


# ---------- FATURA answer key ----------

def _ann(**fields):
    """A FATURA-format annotation; boxes are PDF points from the bottom-left."""
    out = {"TABLE": [[{"bbox": [[10, 300], [500, 200]]}]], "INVOICE_INFO": [], "OTHER": {"text": "x"}, "LOGO": {}}
    y = 800
    for k, text in fields.items():
        out[k] = {"bbox": [[300.0, y], [450.0, y + 12]], "text": text}
        y -= 30
    return out


def test_gold_values_are_typed_and_label_free():
    d = F.gold_from_annotation("Template3_Instance7", _ann(
        NUMBER="INVOICE # INV/30-14/832", DATE="Date: 20-Mar-2008", TOTAL="TOTAL : 734.33 EUR",
        TAX="TAX:VAT (3.88%):  28.18 EUR", BUYER="Bill to:Denise Perez\n1 Main St",
        TOTAL_WORDS="Total in words:  seven hundred and thirt-\ny-four point three three"))
    v = {k: f.value for k, f in d.fields.items()}
    assert v == {"invoice_number": "INV/30-14/832", "invoice_date": "2008-03-20", "total": "734.33",
                 "tax": "28.18", "buyer_name": "Denise Perez", "currency": "EUR", "total_in_words": "734.33"}
    assert d.template_id == 3 and d.unparsed == {}


def test_boxes_are_flipped_to_top_left_and_scaled():
    # Regression guard: FATURA boxes are PDF points from the bottom-left. Unflipped,
    # a title at the top of the page would land at the bottom.
    d = F.gold_from_annotation("Template1_Instance0", {"DATE": {"bbox": [[328.0, 776.4058], [428.704, 788.4058]],
                                                               "text": "Date: 20-Mar-2008"}})
    x0, y0, x1, y1 = d.fields["invoice_date"].bbox
    assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
    assert y0 < 0.1                         # near the top of the page, where it is printed
    assert x0 == pytest.approx(328 / 595, abs=1e-4)


def test_gst_lines_sum_into_tax():
    d = F.gold_from_annotation("Template5_Instance1", {
        "GST(9%)": {"bbox": [[300, 100], [400, 112]], "text": "GST(9%) : 63.18"},
        "GST(18%)": {"bbox": [[300, 80], [400, 92]], "text": "GST(18%) : 10.02"}})
    assert d.fields["tax"].value == "73.20"


def test_unparseable_values_are_reported_not_guessed():
    d = F.gold_from_annotation("Template1_Instance1", _ann(DATE="Date: 03/04/2008"))
    assert "invoice_date" not in d.fields and d.unparsed == {"DATE": "Date: 03/04/2008"}


def _write_fixture(raw, templates=(1, 2, 3, 4), per=2, split_text=None):
    root = raw / "fatura" / "invoices_dataset_final"
    (root / "Annotations" / "Original_Format").mkdir(parents=True)
    (root / "images").mkdir()
    rows = {"train": [], "dev": [], "test": []}
    for t in templates:
        for i in range(per):
            key = f"Template{t}_Instance{i}"
            ann = _ann(NUMBER=f"INVOICE # INV{t}{i:03d}", DATE=f"Date: {10 + i}-Mar-2008",
                       TOTAL=f"TOTAL : {t}{i}.50 $")
            (root / "Annotations" / "Original_Format" / f"{key}.json").write_text(json.dumps(ann))
            (root / "images" / f"{key}.jpg").write_bytes(f"fake-jpeg-{key}".encode())
            rows[("train", "dev", "test")[i % 3]].append(key)
    for split, keys in rows.items():
        (root / f"strat1_{split}.csv").write_text("img_path,annot_path\n" + "".join(f"{k}.jpg,{k}.json\n" for k in keys))
    (root / "Strat2_Split.txt").write_text(split_text or
                                           "train_inds = [1, 2, 3]\ndev_inds = [4]\ntest_inds = dev_inds\n")
    return root


@pytest.fixture
def small_calibration(monkeypatch):
    monkeypatch.setattr(F, "N_CALIBRATION_TEMPLATES", 1)


def test_dev_equal_to_test_becomes_one_holdout(tmp_path, small_calibration):
    root = _write_fixture(tmp_path)
    s = F.read_splits(root)
    assert s.strat2[4] == "holdout"
    assert sorted(v for k, v in s.strat2.items() if k != 4).count("calibration") == 1
    assert s.strat1["Template1_Instance0"] == "train"


def test_overlapping_unseen_split_is_refused(tmp_path, small_calibration):
    root = _write_fixture(tmp_path, split_text="train_inds = [1, 2, 3, 4]\ndev_inds = [4]\ntest_inds = dev_inds\n")
    with pytest.raises(F.FaturaFormatError, match="overlaps"):
        F.read_splits(root)


def test_calibration_split_is_fixed_by_seed(tmp_path, small_calibration):
    root = _write_fixture(tmp_path)
    assert F.read_splits(root).strat2 == F.read_splits(root).strat2


def test_load_writes_gold_to_eval_and_is_idempotent(conn, tmp_path, small_calibration):
    _write_fixture(tmp_path)
    rep = F.load_fatura(conn, tmp_path, origin="test_fixture")
    assert rep.documents == rep.documents_new == 8 and rep.templates == 4 and rep.unparsed == {}
    assert rep.by_split == {"fit": 4, "calibration": 2, "holdout": 2}
    n_gold = conn.execute("SELECT count(*) FROM eval.doc_gold").fetchone()[0]
    assert n_gold == rep.gold_fields == 8 * 4      # number, date, total, currency
    # The engine-readable document row carries no layout answer.
    assert conn.execute("SELECT count(*) FROM source_document WHERE layout_cluster IS NOT NULL").fetchone()[0] == 0

    again = F.load_fatura(conn, tmp_path, origin="test_fixture")
    assert again.documents_new == 0 and again.gold_set_id == rep.gold_set_id
    assert conn.execute("SELECT count(*) FROM eval.doc_gold").fetchone()[0] == n_gold
    assert conn.execute("SELECT count(*) FROM source_document").fetchone()[0] == 8


def test_engine_cannot_read_document_answers(conn, tmp_path, small_calibration):
    _write_fixture(tmp_path)
    F.load_fatura(conn, tmp_path, origin="test_fixture")
    for table in ("eval.doc_gold", "eval.doc_meta", "eval.gold_set"):
        with conn.transaction():
            conn.execute("SET LOCAL ROLE cpgos_engine")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(f"SELECT 1 FROM {table}")
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_engine")
        assert conn.execute("SELECT count(*) FROM source_document").fetchone()[0] == 8


def test_missing_image_is_refused(conn, tmp_path, small_calibration):
    root = _write_fixture(tmp_path)
    (root / "images" / "Template1_Instance0.jpg").unlink()
    with pytest.raises(F.FaturaFormatError, match="image missing"):
        F.load_fatura(conn, tmp_path, origin="test_fixture")
    assert conn.execute("SELECT count(*) FROM source_document").fetchone()[0] == 0


def test_identical_images_are_refused(conn, tmp_path, small_calibration):
    root = _write_fixture(tmp_path)
    (root / "images" / "Template2_Instance0.jpg").write_bytes(b"fake-jpeg-Template1_Instance0")
    with pytest.raises(F.FaturaFormatError, match="byte-identical"):
        F.load_fatura(conn, tmp_path, origin="test_fixture")
