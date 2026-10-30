import json
import shutil
from pathlib import Path

import psycopg
import pytest
from PIL import Image, ImageDraw

from cpgos.documents import extraction, ocr, rules
from cpgos.documents import fatura as F
from cpgos.documents.ocr import Word


def W(text, x0, y0, line, conf=95.0, w=None):
    """A synthetic OCR word: x/y as page fractions, words on one printed line share `line`."""
    w = w or 0.012 * len(text)
    return Word(text, conf, [x0, y0, x0 + w, y0 + 0.012], line)


def words_of(*lines_spec):
    """lines_spec: (text, x0, y0, line_id) per printed line; words laid out left to right."""
    out = []
    for text, x0, y0, lid in lines_spec:
        x = x0
        for tok in text.split():
            out.append(W(tok, x, y0, lid))
            x += 0.012 * len(tok) + 0.008
    return out


# ---------- label vocabulary ----------

@pytest.mark.parametrize("field,raw,label", [
    ("invoice_date", "Invoice Date: 04-May-2007", "Invoice Date"),
    ("invoice_number", "INVOICE # INV/30-14/832", "INVOICE #"),
    ("invoice_number", "INVOICE ID INV38499489", "INVOICE ID"),
    ("tax", "TAX:VAT (3.85%):  10.26 $", "TAX:VAT"),
    ("discount", "DISCOUNT(2.14%): (-)  9.39", "DISCOUNT"),
    ("tax", "GST(18%) : 244.91", "GST"),
    ("po_number", "PO Number :35", "PO Number"),
    ("buyer_name", "Bill to:Denise Perez\n1 Main St", "Bill to"),
    ("buyer_name", "BILL_TO:\nChad Pearson", "BILL_TO"),
    ("total_in_words", "Total in words:  seven hundred", "Total in words"),
    ("seller_name", "Patel-Reid", None),                 # free text is not a label
    ("total", "734.33", None),                           # nothing before the value
])
def test_label_of(field, raw, label):
    assert rules.label_of(field, raw) == label


def test_labels_match_whole_words_only():
    # Regression: "GST" matched inside "GSTIN : 24HDE..." and was read as a tax amount.
    p = rules.label_pattern("GST")
    assert p.search("GST(18%) : 244.91") and not p.search("GSTIN : 24HDE7487")
    assert rules.label_pattern("SUB_TOTAL").search("Sub Total : 5.00")
    assert rules.label_pattern("INVOICE #").search("INVOICE #INV12")


def test_value_must_follow_the_label():
    # Regression: "Note:Total payment due in 14 days" was read as total = 14.00.
    assert rules.value_after("total", " payment due in 14 days") is None
    assert rules.value_after("total", " : 734.33 EUR") is not None
    assert rules.value_after("discount", "(2.14%): (-)  9.39") is not None


def test_gst_lines_are_summed():
    assert rules.find_value("tax", "GST(9%) : 63.18\nGST(9%) : 63.18") == "126.36"


def _model(**lexicon):
    return rules.RulesModel(regions={}, lexicon={f: [(lab, 5)] if isinstance(lab, str) else lab for f, lab in lexicon.items()})


def test_longer_label_wins_and_values_stop_at_the_next_label():
    m = _model(invoice_date=[("Date", 30), ("Invoice Date", 30)], due_date="Due Date", total="TOTAL",
               subtotal="SUB_TOTAL", buyer_name="BILL_TO", invoice_number="INVOICE ID")
    ws = words_of(("Due Date : 19-Nov-2006 Date: 20-Dec-2011", 0.5, 0.10, "1.1.1"),
                  ("SUB_TOTAL : 725.30 EUR", 0.6, 0.80, "2.1.1"),
                  ("TOTAL : 734.33 EUR", 0.6, 0.85, "2.1.2"),
                  ("BILL_TO: INVOICE ID INV57067424", 0.05, 0.30, "3.1.1"),
                  ("Vicki Harvey", 0.05, 0.315, "3.1.2"))
    got = {e.field_name: e.value for e in rules.extract_by_labels(m, ws)}
    assert got["due_date"] == "2006-11-19" and got["invoice_date"] == "2011-12-20"   # "Date" inside "Due Date" ignored
    assert got["subtotal"] == "725.30" and got["total"] == "734.33"                 # "TOTAL" inside "SUB_TOTAL" ignored
    assert got["invoice_number"] == "INV57067424"
    assert got["buyer_name"] == "Vicki Harvey"      # regression: merged columns gave "INVOICE ID INV57067424"
    assert got["currency"] == "EUR"


def test_continuation_line_is_found_in_the_same_column():
    # Regression: OCR lists lines top to bottom across the page, so the right column's
    # "SUB_TOTAL" line sat between "two hundred and fi-" and "fty-four point five".
    m = _model(total_in_words="Total in words", subtotal="SUB_TOTAL")
    ws = words_of(("Total in words: two hundred and fi-", 0.03, 0.700, "1.1.1"),
                  ("SUB_TOTAL : 249.87 $", 0.65, 0.705, "2.1.1"),
                  ("fty-four point five", 0.03, 0.716, "1.1.2"))
    got = {e.field_name: e.value for e in rules.extract_by_labels(m, ws)}
    assert got["total_in_words"] == "254.50" and got["subtotal"] == "249.87"


def test_hyphen_at_line_end_always_continues():
    # Regression: "four hundred and eighty-" parsed on its own as 480.00.
    m = _model(total_in_words="Total in words")
    ws = words_of(("Total in words: four hundred and eighty-", 0.03, 0.70, "1.1.1"),
                  ("-eight point nine seven", 0.03, 0.716, "1.1.2"))
    assert {e.field_name: e.value for e in rules.extract_by_labels(m, ws)}["total_in_words"] == "488.97"


def test_tie_goes_to_the_label_more_layouts_used():
    m = _model(tax=[("TAX:VAT", 14), ("GST", 2)])
    ws = words_of(("TAX:VAT (3.85%): 40.06 $", 0.6, 0.80, "1.1.1"), ("GST(18%) : 104.11", 0.05, 0.85, "2.1.1"))
    assert {e.field_name: e.value for e in rules.extract_by_labels(m, ws)}["tax"] == "40.06"


def test_known_layout_reads_learned_areas_and_falls_back_to_labels():
    m = rules.RulesModel(
        regions={"fatura_L01": {"total": {"bbox": [0.55, 0.84, 0.95, 0.88], "presence": 1.0},
                                "seller_name": {"bbox": [0.0, 0.0, 0.4, 0.05], "presence": 1.0},
                                "invoice_date": {"bbox": [0.5, 0.2, 0.9, 0.22], "presence": 1.0}}},
        lexicon={"invoice_date": [("Date", 10)]})
    ws = words_of(("Fernandez PLC", 0.02, 0.02, "1.1.1"),
                  ("TOTAL : 734.33 EUR", 0.6, 0.85, "2.1.1"),
                  ("Date: 20-Mar-2008", 0.5, 0.40, "3.1.1"))         # printed outside its usual area
    got = {e.field_name: e for e in rules.extract(m, "fatura_L01", ws)}
    assert got["total"].value == "734.33" and got["total"].path == "known_layout"
    assert got["seller_name"].value == "Fernandez PLC"
    assert got["invoice_date"].value == "2008-03-20" and got["invoice_date"].path == "label_search"
    assert got["invoice_date"].signals.get("fallback_from_known_layout")


def test_new_layout_never_guesses_unlabelled_fields():
    m = _model(total="TOTAL")
    ws = words_of(("Fernandez PLC", 0.02, 0.02, "1.1.1"), ("TOTAL : 1.00 $", 0.6, 0.85, "2.1.1"))
    assert "seller_name" not in {e.field_name for e in rules.extract(m, None, ws)}


# ---------- OCR plumbing ----------

def test_tsv_parsing_and_reading_order():
    tsv = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
           "5\t1\t1\t1\t1\t1\t100\t50\t40\t10\t96\tTOTAL\n"
           "5\t1\t1\t1\t1\t2\t150\t50\t60\t10\t91\t734.33\n"
           "5\t1\t2\t1\t1\t1\t10\t10\t80\t10\t88\tINVOICE\n"
           "4\t1\t2\t1\t1\t0\t10\t10\t80\t10\t-1\t\n")
    ws = ocr._parse_tsv(tsv, 1000, 1000)
    assert [w.text for w in ws] == ["TOTAL", "734.33", "INVOICE"]
    assert ws[0].bbox == [0.1, 0.05, 0.14, 0.06]
    assert [t for t, _ in ocr.lines(ws)] == ["INVOICE", "TOTAL 734.33"]


def test_missing_tesseract_says_how_to_install(monkeypatch):
    ocr.tesseract_version.cache_clear()
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(ocr.OcrUnavailable, match="brew install tesseract"):
        ocr.tesseract_version()
    ocr.tesseract_version.cache_clear()


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract not installed")
def test_real_tesseract_reads_a_rendered_line(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "CACHE_ROOT", tmp_path / "cache")
    img = Image.new("L", (595, 200), 255)
    ImageDraw.Draw(img).text((40, 80), "TOTAL : 734.33 EUR", fill=0)
    p = tmp_path / "page.png"
    img.save(p)
    ws = ocr.ocr_image(p, "a" * 64)
    assert "734.33" in " ".join(w.text for w in ws)
    assert (tmp_path / "cache" / ocr.engine_id() / "aa" / ("a" * 64 + ".json")).is_file()
    assert ocr.ocr_image(p, "a" * 64) == ws                        # second call comes from the cache


# ---------- end to end: engine role, idempotency, scoring definitions ----------

def test_extraction_runs_as_engine_and_scores_with_the_key(conn, tmp_path, monkeypatch):
    from tests.test_documents_gold import _write_fixture
    monkeypatch.setattr(F, "N_CALIBRATION_TEMPLATES", 1)
    _write_fixture(tmp_path)
    F.load_fatura(conn, tmp_path, origin="test_fixture")
    model = rules.learn(conn)
    rules.save_model(conn, model)
    monkeypatch.setattr(ocr, "engine_id", lambda: "fake-ocr")

    def fake_ocr(items, workers=None):
        out = []
        for path, _ in items:
            t, i = (int(x) for x in Path(path).stem.replace("Template", "").split("_Instance"))
            total = "9.99" if (t, i) == (1, 0) else f"{t}{i}.50"   # one deliberate misread
            out.append(words_of((f"INVOICE # INV{t}{i:03d}", 0.5, 0.05, "1.1.1"),
                                (f"Date: {10 + i}-Mar-2008", 0.5, 0.08, "1.1.2"),
                                (f"TOTAL : {total} $", 0.5, 0.90, "2.1.1")))
        return out

    monkeypatch.setattr(ocr, "ocr_many", fake_ocr)
    ids = [r[0] for r in conn.execute("SELECT source_document_id FROM source_document ORDER BY 1")]
    first = extraction.extract_documents(conn, tmp_path, ids)
    assert first["documents"] == 8
    assert extraction.extract_documents(conn, tmp_path, ids)["documents"] == 0       # idempotent
    s = extraction.score(conn, ids, first["extractor_version"], conn.execute("SELECT max(gold_set_id) FROM eval.gold_set").fetchone()[0])
    assert s["by_field"]["total"]["wrong"] == 1 and s["by_field"]["total"]["correct"] == 7
    assert s["by_field"]["invoice_number"]["correct"] == 8
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_engine")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE field_extraction SET value = 'x'")
