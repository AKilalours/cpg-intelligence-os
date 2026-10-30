"""CORD v2: 1,000 photographed Indonesian receipts with labelled line items.

Why it is here: FATURA invoices are generated from templates and their totals
are random numbers, so nothing on them can be checked by arithmetic, and none
of them is a photograph. CORD receipts are real photos with real line items,
subtotals, tax, and totals.

Two traps this module handles explicitly:

1. Rupiah amounts. "60.000" means sixty thousand, not sixty; "45,000.00" and
   "9.500,00" both occur. The invoice parser (US conventions) would read
   these wrong, so receipts get their own parser, `parse_idr`, which refuses
   anything ambiguous instead of guessing.
2. The labels themselves. The first test receipt is labelled with a
   discount of 60,000 and a total of 60,000. Before any "the arithmetic must
   add up" rule is scored, `audit` measures how often the published labels
   are internally consistent, so a checker is never blamed for a label error.

The answer key goes to eval.* (hidden from the engine). Images are extracted
from the published Parquet files to data/raw/cord/images/<split>/<id>.png;
each one's sha256 is its identity, as for every other document.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg

DATASET_KEY = "cord"
GOLD_NAME = "cord_receipts"
NORMALIZER_VERSION = "cord-1.0.0"
SPLITS = {"train": "train", "valid": "validation", "validation": "validation", "dev": "validation", "test": "test"}

# Published label -> our field name. Money fields hold rupiah; *_count fields hold counts.
TOTAL_FIELDS = {
    ("sub_total", "subtotal_price"): "subtotal",
    ("sub_total", "discount_price"): "discount",
    ("sub_total", "tax_price"): "tax",
    ("sub_total", "service_price"): "service",
    ("sub_total", "othersvc_price"): "other_service",
    ("total", "total_price"): "total",
    ("total", "cashprice"): "cash",
    ("total", "changeprice"): "change",
    ("total", "creditcardprice"): "card",
    ("total", "emoneyprice"): "emoney",
    ("total", "menuqty_cnt"): "quantity_count",
    ("total", "menutype_cnt"): "item_type_count",
}
FREE_TEXT = {("sub_total", "etc"), ("total", "total_etc")}     # labelled, but not amounts we score


class CordFormatError(RuntimeError):
    pass


# ---------- amounts ----------

_PREFIX = re.compile(r"^(?:[A-Za-z]+\.?|[@*:])\s*")      # "Rp", "Rp.", "IDR", "TOTAL", "@", "*", ":" printed before a number
_DECOR = "[]"                                               # "[25,000]": a box printed around a payment


def parse_idr(raw) -> Decimal | None:
    """Rupiah amount, or None when the text is not an amount or is ambiguous.

    "60.000" -> 60000   "1,944,000" -> 1944000   "45,000.00" -> 45000   "9.500,00" -> 9500
    "17500" -> 17500    "-3,200" -> -3200        "@20,000" -> 20000      "60.00" -> 60
    """
    if raw is None:
        return None
    s = str(raw).strip().strip(_DECOR).strip()
    neg = False
    if s.startswith("(") or s.endswith(")"):              # "(39,800)" and the clipped "39,800)": accounting minus
        neg, s = True, s.strip("()").strip()
    for _ in range(3):                                   # "-Rp 3.200", "Rp -3.200", "TOTAL 47,499", "*46000"
        if s.startswith("-"):
            neg, s = True, s[1:].strip()
        s = _PREFIX.sub("", s)
    if s.endswith("-"):
        neg, s = True, s[:-1].strip()
    if s.startswith(".") and re.fullmatch(r"\.\d{4,}", s):  # ".48000": a stray dot before a plain number
        s = s[1:]
    if re.fullmatch(r"[\d.,]+\.", s) and not re.search(r"[.,]\d{1,2}\.$", s):   # "40,000." trailing full stop
        s = s[:-1]
    s = s.replace(" ", "")
    if not s or not re.fullmatch(r"[\d.,]+", s) or not s[0].isdigit() or not s[-1].isdigit():
        return None
    seps = [c for c in s if c in ".,"]
    if not seps:
        value = Decimal(s)
    else:
        last = max(s.rfind("."), s.rfind(","))
        tail = s[last + 1:]
        if len(set(seps)) == 2:
            dec = s[last]
            head = s[:last]
            if len(tail) not in (1, 2) or dec in head or not _thousands(head, ",." .replace(dec, "")):
                return None
            value = Decimal(head.replace(".", "").replace(",", "") + "." + tail)
        elif len(seps) == 1 and len(tail) in (1, 2):
            value = Decimal(s.replace(",", "."))                  # "60.00", "12,5": a decimal part
        elif _thousands(s, seps[0]):
            value = Decimal(s.replace(seps[0], ""))
        else:
            return None
    return -value if neg else value


def _thousands(s: str, sep: str) -> bool:
    groups = s.split(sep)
    return 1 <= len(groups[0]) <= 3 and all(len(g) == 3 and g.isdigit() for g in groups[1:]) and groups[0].isdigit()


def parse_count(raw) -> Decimal | None:
    """Quantity: "2", "x1", "1X", "2.00", "0,5", "6 Item(s)". Exactly one number must be printed; weights are
    decimals, so "." and "," are decimal points here."""
    if raw is None:
        return None
    nums = re.findall(r"\d+(?:[.,]\d+)?", str(raw))   # "1.00xITEMs", "(Qty=4.00)", "ITEMS :4", "*4 Pcs"
    if len(nums) != 1:
        return None
    s = nums[0]
    try:
        return Decimal(s.replace(",", "."))
    except InvalidOperation:
        return None


def canonical(v: Decimal | None) -> str | None:
    if v is None:
        return None
    v = v.normalize()
    return format(v.quantize(Decimal(1)) if v == v.to_integral_value() else v, "f")


# ---------- one receipt's answer key ----------

@dataclass
class Item:
    name: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    price: Decimal | None
    discount: Decimal | None
    sub_items: list[dict]
    raw: dict


@dataclass
class Receipt:
    doc_key: str
    split: str
    image_id: int
    width: int
    height: int
    fields: dict[str, tuple[str, Decimal | None, list | None]] = field(default_factory=dict)   # name -> (raw, value, bbox)
    items: list[Item] = field(default_factory=list)
    notes: Counter = field(default_factory=Counter)        # label shapes we could not use, by kind

    def value(self, name: str) -> Decimal | None:
        return self.fields[name][1] if name in self.fields else None


def _first_text(v):
    if isinstance(v, list):
        return " ".join(str(x) for x in v if not isinstance(x, dict)).strip() or None
    return None if v is None else str(v).strip()


def _boxes(gt: dict, width: int, height: int) -> dict[str, list]:
    """Category -> [x0, y0, x1, y1] in [0, 1], for categories that occur in exactly one group."""
    groups = defaultdict(set)
    quads = defaultdict(list)
    for line in gt.get("valid_line", []):
        cat = line.get("category")
        groups[cat].add(line.get("group_id"))
        for w in line.get("words", []):
            q = w["quad"]
            quads[cat].append((min(q["x1"], q["x4"]), min(q["y1"], q["y2"]), max(q["x2"], q["x3"]), max(q["y3"], q["y4"])))
    out = {}
    for cat, qs in quads.items():
        if len(groups[cat]) == 1 and qs and width and height:
            x0, y0 = min(q[0] for q in qs), min(q[1] for q in qs)
            x1, y1 = max(q[2] for q in qs), max(q[3] for q in qs)
            out[cat] = [round(max(0, x0) / width, 4), round(max(0, y0) / height, 4),
                        round(min(width, x1) / width, 4), round(min(height, y1) / height, 4)]
    return out


def _item(d: dict) -> Item:
    subs = d.get("sub") or []
    subs = subs if isinstance(subs, list) else [subs]
    sub_items = [{"name": _first_text(s.get("nm")), "quantity": canonical(parse_count(_first_text(s.get("cnt")))),
                  "unit_price": canonical(parse_idr(_first_text(s.get("unitprice")))),
                  "price": canonical(parse_idr(_first_text(s.get("price"))))} for s in subs if isinstance(s, dict)]
    disc = parse_idr(_first_text(d.get("discountprice")))
    return Item(name=" ".join((_first_text(d.get("nm")) or "").split()) or None,
                quantity=parse_count(_first_text(d.get("cnt"))),
                unit_price=parse_idr(_first_text(d.get("unitprice"))),
                price=parse_idr(_first_text(d.get("price"))),
                discount=abs(disc) if disc is not None else None,
                sub_items=sub_items, raw=d)


def gold_from_record(gt: dict) -> Receipt:
    meta = gt.get("meta") or {}
    split = SPLITS.get(meta.get("split"))
    if split is None or "image_id" not in meta:
        raise CordFormatError(f"record without a known split or image id: {meta}")
    size = meta.get("image_size") or {}
    r = Receipt(doc_key=f"{split}/{int(meta['image_id']):04d}", split=split, image_id=int(meta["image_id"]),
                width=int(size.get("width", 0)), height=int(size.get("height", 0)))
    p = gt.get("gt_parse") or {}
    boxes = _boxes(gt, r.width, r.height)
    for block in ("sub_total", "total"):
        parts = p.get(block)
        parts = parts if isinstance(parts, list) else [parts] if parts else []
        for part in parts:
            if not isinstance(part, dict):
                r.notes[f"{block}: not a mapping"] += 1
                continue
            for key, raw in part.items():
                name = TOTAL_FIELDS.get((block, key))
                if name is None:
                    r.notes[f"{block}.{key}: {'free text' if (block, key) in FREE_TEXT else 'unknown label'}"] += 1
                    continue
                if isinstance(raw, list) or name in r.fields:
                    r.notes[f"{name}: labelled more than once"] += 1
                    r.fields.pop(name, None)
                    continue
                text = str(raw).strip()
                val = parse_count(text) if name.endswith("_count") else parse_idr(text)
                if val is None:
                    r.notes[f"{name}: not parseable"] += 1
                if name == "discount" and val is not None:
                    val = abs(val)                         # labelled with or without a minus sign
                r.fields[name] = (text, val, boxes.get(f"{block}.{key}"))
    menu = p.get("menu")
    menu = menu if isinstance(menu, list) else [menu] if menu else []
    r.items = [_item(d) for d in menu if isinstance(d, dict)]
    if p.get("void_menu"):
        r.notes["void_menu: ignored"] += 1
    return r


# ---------- do the labels add up? ----------

ROUNDING = Decimal(100)        # Indonesian receipts round totals (96,003 is printed 96,000); differences up to Rp 100


def _match(target: Decimal | None, candidates) -> str | None:
    """'exact', 'rounded' (off by at most Rp 100), 'fails', or None when the target is missing."""
    cands = [c for c in candidates if c is not None]
    if target is None or not cands:
        return None
    diff = min(abs(target - c) for c in cands)
    return "exact" if diff == 0 else "rounded" if diff <= ROUNDING else "fails"


def audit(r: Receipt) -> dict[str, str | None]:
    """Each check is 'exact', 'rounded', 'fails', or None (not applicable: a needed value is missing).

    items_sum:        line amounts (with add-ons, or less line discounts) equal the subtotal, or the total
                      (menu prices that include tax, or lines + tax + service - discount)
    subtotal_to_total: subtotal - discount + service (+ tax, unless prices include it) = total
    change:           cash - total = change (a printed minus sign on the change is ignored)
    line_amount:      quantity x unit price (+ add-ons, - line discount) = line amount, for every line that has all three
    """
    sub, total = r.value("subtotal"), r.value("total")
    disc = r.value("discount") or Decimal(0)
    extras = sum((r.value(f) or Decimal(0) for f in ("service", "other_service")), Decimal(0))
    tax = r.value("tax") or Decimal(0)
    out: dict[str, str | None] = {}

    prices = [i.price for i in r.items]
    if r.items and all(p is not None for p in prices):
        plain = sum(prices, Decimal(0))
        subs = sum((Decimal(x["price"]) for i in r.items for x in i.sub_items if x["price"]), Decimal(0))
        line_disc = sum((i.discount or Decimal(0) for i in r.items), Decimal(0))
        sums = (plain, plain + subs, plain - line_disc, plain + subs - line_disc)
        charged = tuple(x - disc + tax + extras for x in sums)          # no subtotal printed: lines + tax + service
        res = [_match(sub, sums), _match(total, sums + charged)]
        res = [x for x in res if x is not None]
        out["items_sum"] = ("exact" if "exact" in res else "rounded" if "rounded" in res else "fails") if res else None
    else:
        out["items_sum"] = None

    out["subtotal_to_total"] = (_match(total, (sub - disc + tax + extras, sub - disc + extras))
                                if sub is not None else None)

    cash, change = r.value("cash"), r.value("change")
    out["change"] = None if None in (cash, change) else _match(abs(change), (cash - total,) if total is not None else ())

    worst = None
    for i in r.items:
        if None in (i.quantity, i.unit_price, i.price):
            continue
        subs = sum((Decimal(x["price"]) for x in i.sub_items if x["price"]), Decimal(0))
        q = i.quantity * i.unit_price
        m = _match(i.price, (q, q + subs, q - (i.discount or 0), q + subs - (i.discount or 0)))
        worst = m if worst is None or ["exact", "rounded", "fails"].index(m) > ["exact", "rounded", "fails"].index(worst) else worst
    out["line_amount"] = worst
    return out


def audit_summary(receipts: list[Receipt]) -> dict:
    """Per split: for each check, how many applicable receipts add up exactly, and exactly-or-after-rounding."""
    from cpgos.finance.evaluate import rate
    out = {}
    for split in ("train", "validation", "test", "all"):
        rs = [r for r in receipts if split == "all" or r.split == split]
        checks = defaultdict(lambda: Counter())
        failing = defaultdict(list)
        consistent = applicable = 0
        for r in rs:
            a = audit(r)
            for name, res in a.items():
                if res is None:
                    continue
                checks[name][res] += 1
                if res == "fails" and len(failing[name]) < 10:
                    failing[name].append(r.doc_key)
            decided = [x for x in a.values() if x is not None]
            if decided:
                applicable += 1
                consistent += "fails" not in decided
        out[split] = {
            "receipts": len(rs),
            "all_checks_pass_allowing_rounding": rate(consistent, applicable),
            "checks": {k: {"exact": rate(c["exact"], sum(c.values())),
                           "exact_or_rounded": rate(c["exact"] + c["rounded"], sum(c.values())),
                           "examples_failing": failing[k]} for k, c in sorted(checks.items())}}
    return out


# ---------- loading ----------

@dataclass
class LoadReport:
    receipts: int = 0
    documents_new: int = 0
    images_written: int = 0
    by_split: dict = field(default_factory=dict)
    gold_set_id: int | None = None
    fields: int = 0
    items: int = 0
    label_notes: dict = field(default_factory=dict)
    unparsed_values: int = 0
    duplicates_dropped: list = field(default_factory=list)


def gold_version() -> str:
    """Changes whenever this module's normalisation code changes."""
    return NORMALIZER_VERSION + ":" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]


def read_records(parquet_dir: Path):
    """Yield (image_bytes, ground_truth dict) from the published Parquet files, in file order."""
    import duckdb
    files = sorted(parquet_dir.glob("*.parquet"))
    if not files:
        raise CordFormatError(f"no Parquet files in {parquet_dir}; run: make download DATASET=cord")
    con = duckdb.connect()
    # Each published file is one 450-490 MB row group, which must be decoded whole: one thread, rows
    # handed over in small batches, so peak memory stays near one file instead of 2.3 GB.
    con.execute("SET threads = 1")
    con.execute("SET memory_limit = '2GB'")
    for f in files:
        cur = con.execute("SELECT image.bytes, ground_truth FROM read_parquet(?)", [str(f)])
        while batch := cur.fetchmany(16):
            for img, gt in batch:
                yield bytes(img), json.loads(gt)


def load_cord(conn: psycopg.Connection, raw_root: Path, *, file_shas: dict | None = None) -> tuple[LoadReport, list[Receipt]]:
    """Extract images, register each as a source_document, write the answer key to eval.*.
    Idempotent: re-running with the same files and the same normaliser changes nothing."""
    src = raw_root / "cord" / "data"
    out_dir = raw_root / "cord" / "images"
    # Pass 1: answer keys and image hashes only (no image is kept in memory); refuse problems before writing.
    docs: list[tuple[Receipt, None, str]] = []
    for img, gt in read_records(src):
        if img[:8] != b"\x89PNG\r\n\x1a\n":
            raise CordFormatError("expected PNG image bytes")
        docs.append((gold_from_record(gt), None, hashlib.sha256(img).hexdigest()))
    keys = Counter(r.doc_key for r, _, _ in docs)
    if any(n > 1 for n in keys.values()):
        raise CordFormatError(f"duplicate receipt ids: {[k for k, n in keys.items() if n > 1][:5]}")
    # One image, two answer keys. CORD v2 has 2 such pairs, both inside train, labelled twice by
    # different hands ("10,000" vs "10.000"). Within a split: keep the first, drop the copy, report it.
    # Across splits it would leak test answers into training: refuse before anything is written.
    first: dict[str, Receipt] = {}
    dropped = []
    for r, _, d in sorted(docs, key=lambda x: x[0].doc_key):
        if d in first:
            if first[d].split != r.split:
                raise CordFormatError(f"{first[d].doc_key} and {r.doc_key} are the same image in different splits")
            dropped.append({"dropped": r.doc_key, "kept": first[d].doc_key})
        else:
            first[d] = r
    docs = [(r, None, d) for d, r in first.items()]

    rep = LoadReport(receipts=len(docs), by_split=dict(Counter(r.split for r, _, _ in docs)), duplicates_dropped=dropped)
    manifest = hashlib.sha256("".join(f"{k}={v}\n" for k, v in sorted((file_shas or {}).items())).encode()
                              if file_shas else "".join(sorted(d for _, _, d in docs)).encode()).hexdigest()
    # Pass 2: write each image once; an existing file is kept only if its hash matches.
    expected = {d: r.doc_key for r, _, d in docs}
    for img, gt in read_records(src):
        digest = hashlib.sha256(img).hexdigest()
        if gold_from_record(gt).doc_key != expected[digest]:
            continue                                                  # the dropped copy
        path = out_dir / f"{expected[digest]}.png"
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(img)
            rep.images_written += 1

    with conn.transaction():
        ver = conn.execute(
            """INSERT INTO dataset_version (dataset_key, file_name, sha256, license, data_origin)
               VALUES (%s, %s, %s, 'CC BY 4.0', 'real')
               ON CONFLICT (dataset_key, sha256) DO UPDATE SET file_name = EXCLUDED.file_name
               RETURNING dataset_version_id""",
            (DATASET_KEY, "naver-clova-ix/cord-v2 parquet" if file_shas else f"manifest of {len(docs)} images",
             manifest)).fetchone()[0]
        gs = conn.execute(
            """INSERT INTO eval.gold_set (name, dataset_version_id, gold_version, notes) VALUES (%s, %s, %s, %s)
               ON CONFLICT (name, dataset_version_id, gold_version) DO UPDATE SET notes = EXCLUDED.notes
               RETURNING gold_set_id""",
            (GOLD_NAME, ver, gold_version(), "Totals block and line items; rupiah; official train/validation/test")).fetchone()[0]
        rep.gold_set_id = gs
        existing = dict(conn.execute("SELECT sha256, source_document_id FROM source_document WHERE sha256 = ANY(%s)",
                                     ([d for _, _, d in docs],)).fetchall())
        new = [(d, f"cord/images/{r.doc_key}.png") for r, _, d in docs if d not in existing]
        if new:
            with conn.cursor().copy("COPY source_document (sha256, storage_key, dataset_version_id, source_type, mime_type) "
                                    "FROM STDIN") as cp:
                for d, key in new:
                    cp.write_row((d, key, ver, "receipt", "image/png"))
        rep.documents_new = len(new)
        ids = dict(conn.execute("SELECT sha256, source_document_id FROM source_document WHERE sha256 = ANY(%s)",
                                ([d for _, _, d in docs],)).fetchall())
        conn.execute("DELETE FROM eval.receipt_gold WHERE gold_set_id = %s", (gs,))
        conn.execute("DELETE FROM eval.receipt_item_gold WHERE gold_set_id = %s", (gs,))
        notes = Counter()
        with conn.cursor().copy("COPY eval.receipt_gold (gold_set_id, source_document_id, field_name, raw_text, value, bbox) "
                                "FROM STDIN") as cp:
            for r, _, d in docs:
                for name, (raw, val, box) in r.fields.items():
                    cp.write_row((gs, ids[d], name, raw, canonical(val), json.dumps(box) if box else None))
                    rep.fields += 1
                    rep.unparsed_values += val is None
                notes.update(r.notes)
        with conn.cursor().copy("COPY eval.receipt_item_gold (gold_set_id, source_document_id, item_index, name, quantity, "
                                "unit_price, price, discount, sub_items, raw) FROM STDIN") as cp:
            for r, _, d in docs:
                for n, it in enumerate(r.items):
                    cp.write_row((gs, ids[d], n, it.name, it.quantity, it.unit_price, it.price, it.discount,
                                  json.dumps(it.sub_items), json.dumps(it.raw, ensure_ascii=False)))
                    rep.items += 1
        conn.execute("""INSERT INTO eval.receipt_meta (source_document_id, dataset_key, doc_key, split, width, height)
                        SELECT * FROM unnest(%s::bigint[], %s::text[], %s::text[], %s::text[], %s::int[], %s::int[])
                        ON CONFLICT (source_document_id) DO NOTHING""",
                     ([ids[d] for _, _, d in docs], [DATASET_KEY] * len(docs), [r.doc_key for r, _, _ in docs],
                      [r.split for r, _, _ in docs], [r.width for r, _, _ in docs], [r.height for r, _, _ in docs]))
        rep.label_notes = dict(notes.most_common())
    return rep, [r for r, _, _ in docs]
