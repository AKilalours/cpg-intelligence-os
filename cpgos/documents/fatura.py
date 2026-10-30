"""FATURA answer key: typed gold values, fixed splits, and provenance.

FATURA (Zenodo 10371464, CC BY 4.0) is 10,000 invoice images from 50 layouts,
200 per layout, generated from templates. Facts about it that shape the
evaluation, each checked on the full dataset:

- Boxes are PDF points from the bottom-left corner on a 595 x 841 page; they are
  flipped and scaled to [0, 1] from the top-left here.
- Values are stored with their printed label ("Invoice Date: 04-May-2007"), so
  the gold value is normalised by cpgos.documents.normalize, the same code that
  normalises every extractor's output.
- Line items are printed but not labelled: FATURA scores header fields only.
- Totals are random: subtotal - discount + tax = total holds on 8 of 5,597
  invoices. Arithmetic checks are therefore not scored on FATURA.
- Dates are random too (due dates before invoice dates are common), so FATURA
  scores reading a date, not judging whether it is plausible.
- The official unseen-layout split (Strat2) lists the same 10 layouts as dev
  and as test. They are merged into one "holdout", scored once at the end, and
  never used for tuning. The 40 training layouts are divided into "fit" (32)
  and "calibration" (8, also unseen by fitting) by a fixed seed.

The answer key goes to the eval schema, which the engine role cannot read. The
true layout id is kept there as well: source_document.layout_cluster is
readable by the engine, and putting the answer in it would hand the layout
router its own test.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from cpgos.documents import normalize as nz

PAGE_W, PAGE_H = 595.0, 841.0
CALIBRATION_SEED = 2026
N_CALIBRATION_TEMPLATES = 8

# Canonical field -> (value type, FATURA labels it can come from, in order of preference).
# Value types come from normalize.FIELD_TYPES; the assertion below keeps the two in step.
FIELDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "invoice_number": ("id", ("NUMBER",)),
    "invoice_date": ("date", ("DATE",)),
    "due_date": ("date", ("DUE_DATE",)),
    "po_number": ("id", ("PO_NUMBER",)),
    "seller_name": ("text", ("SELLER_NAME",)),
    "buyer_name": ("name", ("BUYER", "BILL_TO")),
    "subtotal": ("money", ("SUB_TOTAL",)),
    "discount": ("money", ("DISCOUNT",)),
    "tax": ("money", ("TAX",)),                  # or the sum of GST(x%) lines, see _tax
    "total": ("money", ("TOTAL",)),
    "amount_due": ("money", ("AMOUNT_DUE",)),
    "currency": ("currency", ("TOTAL", "SUB_TOTAL", "AMOUNT_DUE", "TAX")),
    "total_in_words": ("money_words", ("TOTAL_WORDS",)),
}
FIELD_TYPES = nz.FIELD_TYPES
assert set(FIELDS) == set(FIELD_TYPES), "FATURA fields and canonical fields diverged"


@dataclass
class GoldField:
    name: str
    raw_text: str
    value: str                      # canonical string: ISO date, "734.33", "USD", "INV/30-14/832"
    bbox: list[float] | None        # [x0, y0, x1, y1] in [0, 1], top-left origin


@dataclass
class GoldDoc:
    doc_key: str                    # "Template7_Instance100"
    template_id: int
    image_rel: str                  # path relative to data/raw
    fields: dict[str, GoldField] = field(default_factory=dict)
    unparsed: dict[str, str] = field(default_factory=dict)   # label -> raw text the normaliser refused


class FaturaFormatError(ValueError):
    pass


def _box(b) -> list[float] | None:
    try:
        (x0, y0), (x1, y1) = b
    except (TypeError, ValueError):
        return None
    top, bottom = PAGE_H - max(y0, y1), PAGE_H - min(y0, y1)
    clamp = lambda v: round(min(max(v, 0.0), 1.0), 5)
    return [clamp(min(x0, x1) / PAGE_W), clamp(top / PAGE_H), clamp(max(x0, x1) / PAGE_W), clamp(bottom / PAGE_H)]


def _union(boxes: list[list[float]]) -> list[float] | None:
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _entry(ann: dict, label: str) -> tuple[str, list[float] | None] | None:
    v = ann.get(label)
    if isinstance(v, dict) and isinstance(v.get("text"), str) and v["text"].strip():
        return v["text"], _box(v.get("bbox"))
    return None


def _tax(ann: dict) -> tuple[str, str, list[float] | None] | None:
    e = _entry(ann, "TAX")
    if e:
        amt = nz.parse_amount(e[0], last=True)
        return (e[0], str(amt), e[1]) if amt is not None else None
    gst = [(k, _entry(ann, k)) for k in sorted(ann) if re.fullmatch(r"GST\(\d+(?:\.\d+)?%\)", k)]
    gst = [(k, e) for k, e in gst if e]
    if not gst:
        return None
    amounts = [nz.parse_amount(e[0], last=True) for _, e in gst]
    if any(a is None for a in amounts):
        return None
    return " | ".join(e[0] for _, e in gst), str(sum(amounts)), _union([e[1] for _, e in gst])


def gold_from_annotation(doc_key: str, ann: dict) -> GoldDoc:
    m = re.fullmatch(r"Template(\d+)_Instance(\d+)", doc_key)
    if not m:
        raise FaturaFormatError(f"unexpected document key {doc_key!r}")
    doc = GoldDoc(doc_key, int(m.group(1)), f"fatura/invoices_dataset_final/images/{doc_key}.jpg")
    for name, (kind, labels) in FIELDS.items():
        if name == "tax":
            t = _tax(ann)
            if t:
                doc.fields[name] = GoldField(name, t[0], t[1], t[2])
            continue
        for label in labels:
            e = _entry(ann, label)
            if not e:
                continue
            raw, box = e
            value = nz.normalize_field(name, raw)
            if value is None:
                if kind != "currency":            # currency is optional on every amount line
                    doc.unparsed[label] = raw
                continue
            doc.fields[name] = GoldField(name, raw, value, box)
            break
    return doc


@dataclass
class Splits:
    strat1: dict[str, str]          # doc_key -> train | dev | test   (same layouts everywhere)
    strat2: dict[int, str]          # template_id -> fit | calibration | holdout   (unseen layouts)


def read_splits(root: Path) -> Splits:
    strat1 = {}
    for split in ("train", "dev", "test"):
        p = root / f"strat1_{split}.csv"
        with p.open(newline="") as f:
            for r in csv.DictReader(f):
                strat1[r["img_path"].rsplit(".", 1)[0]] = split
    text = (root / "Strat2_Split.txt").read_text()
    lists = {}
    for name, body in re.findall(r"(\w+)\s*=\s*\[([^\]]*)\]", text):
        lists[name] = [int(x) for x in re.findall(r"\d+", body)]
    alias = re.search(r"test_inds\s*=\s*dev_inds", text)
    train, dev = lists.get("train_inds"), lists.get("dev_inds")
    test = dev if alias else lists.get("test_inds")
    if not train or not dev:
        raise FaturaFormatError("Strat2_Split.txt: train_inds or dev_inds missing")
    if set(train) & set(dev) or (test and set(test) - set(dev) and set(test) & set(train)):
        raise FaturaFormatError("Strat2 split overlaps between training and evaluation layouts")
    held = sorted(set(dev) | set(test or []))
    calib = set(random.Random(CALIBRATION_SEED).sample(sorted(train), N_CALIBRATION_TEMPLATES))
    strat2 = {t: ("calibration" if t in calib else "fit") for t in train}
    strat2.update({t: "holdout" for t in held})
    return Splits(strat1, strat2)


def gold_version(docs: list["GoldDoc"]) -> str:
    """Hash of the answer key's content: the same values give the same version even after
    a refactor; any changed value, box, or document gives a new one."""
    h = hashlib.sha256()
    for d in sorted(docs, key=lambda x: x.doc_key):
        for f in sorted(d.fields.values(), key=lambda x: x.name):
            h.update(json.dumps([d.doc_key, f.name, f.value, f.bbox]).encode())
    return h.hexdigest()[:16]


@dataclass
class LoadReport:
    documents: int = 0
    documents_new: int = 0
    gold_fields: int = 0
    unparsed: dict[str, int] = field(default_factory=dict)
    by_split: dict[str, int] = field(default_factory=dict)
    templates: int = 0
    gold_set_id: int | None = None


def load_fatura(conn: psycopg.Connection, raw_root: Path, *, origin: str = "real_template_generated",
                zip_sha256: str | None = None, limit: int | None = None) -> LoadReport:
    """Register every invoice image as a source_document and write its answer key to eval.*.
    Idempotent: re-running with the same files and the same normaliser changes nothing."""
    root = raw_root / "fatura" / "invoices_dataset_final"
    ann_dir = root / "Annotations" / "Original_Format"
    if not ann_dir.is_dir():
        raise FaturaFormatError(f"{ann_dir} not found; download FATURA first (make download DATASET=fatura)")
    splits = read_splits(root)
    paths = sorted(ann_dir.glob("Template*_Instance*.json"), key=lambda p: [int(x) for x in re.findall(r"\d+", p.stem)])
    if limit:
        paths = paths[:limit]

    manifest = hashlib.sha256()
    docs: list[tuple[GoldDoc, str]] = []
    for p in paths:
        doc = gold_from_annotation(p.stem, json.loads(p.read_text()))
        img = raw_root / doc.image_rel
        if not img.is_file():
            raise FaturaFormatError(f"image missing for {doc.doc_key}: {img}")
        digest = hashlib.sha256(img.read_bytes()).hexdigest()
        manifest.update(digest.encode() + hashlib.sha256(p.read_bytes()).digest())
        if doc.template_id not in splits.strat2:
            raise FaturaFormatError(f"{doc.doc_key}: layout {doc.template_id} is in no Strat2 split")
        docs.append((doc, digest))

    dupes = len(docs) - len({d for _, d in docs})
    if dupes:   # one image, two answer keys: refuse before anything is written
        raise FaturaFormatError(f"{dupes} FATURA images are byte-identical to another; refusing ambiguous gold")

    rep = LoadReport(documents=len(docs), templates=len({d.template_id for d, _ in docs}))
    version_sha = zip_sha256 or manifest.hexdigest()
    gv = gold_version([d for d, _ in docs])
    with conn.transaction():
        ver = conn.execute(
            """INSERT INTO dataset_version (dataset_key, file_name, sha256, license, data_origin)
               VALUES ('fatura', %s, %s, 'CC BY 4.0', %s)
               ON CONFLICT (dataset_key, sha256) DO UPDATE SET file_name = EXCLUDED.file_name
               RETURNING dataset_version_id""",
            ("FATURA2.zip" if zip_sha256 else f"manifest of {len(docs)} images+annotations", version_sha, origin),
        ).fetchone()[0]
        gs = conn.execute(
            """INSERT INTO eval.gold_set (name, dataset_version_id, gold_version, notes)
               VALUES ('fatura_header_fields', %s, %s, %s)
               ON CONFLICT (name, dataset_version_id, gold_version) DO UPDATE SET notes = EXCLUDED.notes
               RETURNING gold_set_id""",
            (ver, gv, "Header fields only; totals and dates are random in FATURA; Strat2 dev = test merged into holdout"),
        ).fetchone()[0]
        rep.gold_set_id = gs

        existing = dict(conn.execute("SELECT sha256, source_document_id FROM source_document").fetchall())
        new_rows = [(digest, doc.image_rel, ver) for doc, digest in docs if digest not in existing]
        if new_rows:
            with conn.cursor().copy(
                    "COPY source_document (sha256, storage_key, dataset_version_id, source_type, mime_type) FROM STDIN") as cp:
                for digest, key, v in new_rows:
                    cp.write_row((digest, key, v, "invoice", "image/jpeg"))
        rep.documents_new = len(new_rows)
        ids = dict(conn.execute(
            "SELECT sha256, source_document_id FROM source_document WHERE sha256 = ANY(%s)",
            ([d for _, d in docs],)).fetchall())
        if len(ids) != len({d for _, d in docs}):
            raise FaturaFormatError("not every image was registered")

        conn.execute("DELETE FROM eval.doc_gold WHERE gold_set_id = %s", (gs,))
        buf_meta, n_gold = [], 0
        with conn.cursor().copy(
                "COPY eval.doc_gold (gold_set_id, source_document_id, field_name, raw_text, value, bbox) FROM STDIN") as cp:
            for doc, digest in docs:
                sid = ids[digest]
                for f in doc.fields.values():
                    cp.write_row((gs, sid, f.name, f.raw_text, f.value, json.dumps(f.bbox) if f.bbox else None))
                    n_gold += 1
                for label in doc.unparsed:
                    rep.unparsed[label] = rep.unparsed.get(label, 0) + 1
                s1 = splits.strat1.get(doc.doc_key)
                s2 = splits.strat2[doc.template_id]
                rep.by_split[s2] = rep.by_split.get(s2, 0) + 1
                buf_meta.append((sid, "fatura", doc.doc_key, doc.template_id, s1, s2))
        rep.gold_fields = n_gold
        conn.execute("""INSERT INTO eval.doc_meta (source_document_id, dataset_key, doc_key, layout_id, split_seen, split_unseen)
                        SELECT * FROM unnest(%s::bigint[], %s::text[], %s::text[], %s::int[], %s::text[], %s::text[])
                        ON CONFLICT (source_document_id) DO UPDATE SET
                            doc_key = EXCLUDED.doc_key, layout_id = EXCLUDED.layout_id,
                            split_seen = EXCLUDED.split_seen, split_unseen = EXCLUDED.split_unseen""",
                     [list(c) for c in zip(*buf_meta)])
    return rep
