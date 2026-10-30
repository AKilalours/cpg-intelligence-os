"""Tier 1 extraction: OCR plus rules. Cheap, local, deterministic.

Analogy: a clerk who has processed a supplier's invoices for months knows the
total is always in the bottom-right box (the known-layout path). Given an
unfamiliar invoice, the same clerk looks for printed labels such as "TOTAL"
or "Invoice Date" and reads what follows (the new-layout path). When unsure,
the clerk leaves the field blank rather than guessing; the next tier, a
model, gets those.

Everything the rules know is learned from the fit layouts' training answers:
- field areas per known layout: where each field was printed on its training
  invoices, slightly enlarged;
- the label vocabulary per field: the printed labels that preceded each value.
The new-layout path therefore uses no knowledge of the calibration or holdout
layouts, and no hand-written list of labels.

Values are typed by cpgos.documents.normalize, the same code that types the
answer key, so an extraction is right only when its typed value is equal.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

import psycopg
from psycopg.types.json import Jsonb

from cpgos.documents import normalize as nz
from cpgos.documents.ocr import Word, lines

RULES_VERSION = "rules-1.0.0"
REGION_PAD_X, REGION_PAD_Y = 0.012, 0.006       # enlarge learned field areas a little
MIN_PRESENCE = 0.5                               # a layout "has" a field if it appears on half its invoices
COLUMN_TOLERANCE = 0.02                          # left edges within 2% of page width = same column
MAX_LINE_GAP = 0.03                              # a continuation line starts within 3% of page height

_DATE_PATTERNS = [
    r"\b\d{1,2}[-\s/.][A-Za-z]{3,9}[-\s/.,]*\d{4}\b",
    r"\b[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}\b",
    r"\b\d{4}-\d{2}-\d{2}\b",
    r"\b\d{1,2}[/.]\d{1,2}[/.]\d{4}\b",
]


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9#]", "", s.lower())


def label_of(field_name: str, raw: str) -> str | None:
    """The printed label before a value, or None when nothing labels it.

    "Invoice Date: 04-May-2007" -> "Invoice Date"; "INVOICE # INV/30-14/832" -> "INVOICE #";
    "TAX:VAT (3.85%):  10.26 $" -> "TAX:VAT"; "DISCOUNT(2.14%): (-) 9.39" -> "DISCOUNT";
    "Bill to:Denise Perez" -> "Bill to"; "Patel-Reid" (a seller name) -> None.
    A label must be followed by a value; free text such as a company name is not a label."""
    kind = nz.FIELD_TYPES[field_name]
    if kind in ("text", "currency"):
        return None
    first = raw.strip().splitlines()[0] if raw.strip() else ""
    if not first:
        return None
    if kind in ("name", "money_words"):
        if ":" not in first:
            return None
        label, rest = first.split(":", 1)
        rest = rest.strip() or "\n".join(raw.strip().splitlines()[1:]).strip()
    else:
        toks, rest_toks = [], []
        for i, t in enumerate(first.replace("(", " (").split()):
            if re.search(r"[\d$€£]", t):
                rest_toks = first.replace("(", " (").split()[i:]
                break
            toks.append(t)
        label, rest = " ".join(toks), " ".join(rest_toks)
    label = re.sub(r"\(.*?\)", "", label).strip(" :;.-")
    if not rest or not _squash(label):
        return None
    return label


_LEAD = re.compile(r"^(?:\s|[:#.\-–=;]|\(\s*-\s*\)|\(\s*[\d.,]+\s*%\s*\))*")
_STARTS = {"money": r"[$€£\d]", "date": r"[\dA-Za-z]", "id": r"[A-Za-z0-9]", "name": r"[A-Za-z]",
           "money_words": r"[A-Za-z]", "currency": r".", "text": r"."}


def label_pattern(label: str) -> re.Pattern:
    """Whole-word, case-insensitive match; spaces and underscores interchangeable ("SUB_TOTAL" = "Sub Total")."""
    toks = [t for t in re.split(r"[\s_]+", label) if t]
    body = r"[\s_]*".join(re.escape(t) for t in toks)
    tail = r"(?![A-Za-z0-9])" if label[-1].isalnum() else ""
    return re.compile(r"(?<![A-Za-z0-9])" + body + tail, re.IGNORECASE)


def label_hits(text: str, pats) -> list[tuple[int, int, str, str, int]]:
    """Labels printed in a line, longest first, without overlaps: "TOTAL" inside "SUB_TOTAL" and
    "Date" inside "Due Date" are not counted. pats: [(field, label, layouts, compiled pattern)].
    Returns (start, end, field, label, layouts) sorted by position. Shared by every caller, so
    the rules tier and the model grounding check can never disagree about what labels a line."""
    hits = [(m.start(), m.end(), f, lab, n) for f, lab, n, p in pats for m in p.finditer(text)]
    hits.sort(key=lambda h: (-(h[1] - h[0]), h[0]))
    kept: list[tuple[int, int, str, str, int]] = []
    for h in hits:
        if not any(h[0] < k[1] and h[1] > k[0] for k in kept):
            kept.append(h)
    return sorted(kept)


def lexicon_patterns(lexicon: dict) -> list[tuple[str, str, int, re.Pattern]]:
    return [(f, lab, n, label_pattern(lab)) for f, labs in lexicon.items() for lab, n in labs]


def value_after(field_name: str, rest: str) -> str | None:
    """The text after a label, if a value of the right kind starts right there."""
    kind = nz.FIELD_TYPES[field_name]
    body = _LEAD.sub("", rest)
    if not body or not re.match(_STARTS[kind], body):
        return None
    return body


def find_value(field_name: str, text: str) -> str | None:
    """The typed value for a field inside a piece of printed text, or None."""
    kind = nz.FIELD_TYPES[field_name]
    if not text or not text.strip():
        return None
    if kind == "date":
        for pat in _DATE_PATTERNS:
            for m in re.finditer(pat, text):
                v = nz.normalize_field(field_name, m.group(0))
                if v:
                    return v
        return None
    if field_name == "tax":
        gst = re.findall(r"GST\s*\(\s*\d+(?:\.\d+)?\s*%\s*\)\s*:?\s*([\d.,]+)", text, flags=re.I)
        if len(gst) > 1:
            amounts = [nz.parse_amount(g) for g in gst]
            return str(sum(amounts)) if all(a is not None for a in amounts) else None
    if kind == "id":
        rest = (nz.strip_label(text.splitlines()[0]) or "").lstrip(" :;#.-")
        tok = rest.split()[0] if rest.split() else ""
        return nz.normalize_id(tok) if tok and re.search(r"\d", tok) else None
    return nz.normalize_field(field_name, text)


@dataclass
class RulesModel:
    regions: dict[str, dict[str, dict]]          # layout_key -> field -> {"bbox": [...], "presence": float}
    lexicon: dict[str, list[tuple[str, int]]]    # field -> [(label, layouts using it)], most used first
    version: str = RULES_VERSION
    trained_on: dict = field(default_factory=dict)

    def content_sha256(self) -> str:
        return hashlib.sha256(json.dumps([self.version, self.regions, self.lexicon], sort_keys=True).encode()).hexdigest()


def learn(conn: psycopg.Connection, gold_set_id: int | None = None) -> RulesModel:
    """Field areas and label vocabulary from the fit layouts' training invoices only."""
    gs = gold_set_id or conn.execute("SELECT max(gold_set_id) FROM eval.gold_set WHERE name = 'fatura_header_fields'").fetchone()[0]
    rows = conn.execute(
        """SELECT m.layout_id, g.field_name, g.raw_text, g.bbox, m.source_document_id
           FROM eval.doc_gold g JOIN eval.doc_meta m USING (source_document_id)
           WHERE g.gold_set_id = %s AND m.split_unseen = 'fit' AND m.split_seen = 'train'""", (gs,)).fetchall()
    if not rows:
        raise RuntimeError("no training answers; run: make load-fatura")
    docs_per_layout = Counter()
    for lid, n in conn.execute(
            """SELECT layout_id, count(*) FROM eval.doc_meta WHERE split_unseen = 'fit' AND split_seen = 'train'
               GROUP BY layout_id""").fetchall():
        docs_per_layout[lid] = n
    boxes: dict[tuple[int, str], list] = defaultdict(list)
    labels: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for lid, f, raw, bbox, _ in rows:
        if bbox:
            boxes[(lid, f)].append(bbox if isinstance(bbox, list) else json.loads(bbox))
        lab = label_of(f, raw)
        if lab:
            labels[f][lab].add(lid)
    regions: dict[str, dict[str, dict]] = defaultdict(dict)
    for (lid, f), bs in boxes.items():
        presence = len(bs) / docs_per_layout[lid]
        if presence < MIN_PRESENCE:
            continue
        regions[f"fatura_L{lid:02d}"][f] = {
            "bbox": [round(max(0.0, min(b[0] for b in bs) - REGION_PAD_X), 5), round(max(0.0, min(b[1] for b in bs) - REGION_PAD_Y), 5),
                     round(min(1.0, max(b[2] for b in bs) + REGION_PAD_X), 5), round(min(1.0, max(b[3] for b in bs) + REGION_PAD_Y), 5)],
            "presence": round(presence, 4)}
    # One printed label, one field: when the answer key uses a label for several fields
    # (FATURA labels "Due Date" as the invoice date on one layout), it goes to the field
    # that uses it on the most layouts; a tie is dropped as ambiguous.
    owners: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for f, by in labels.items():
        for lab, ls in by.items():
            owners[_squash(lab)].append((len(ls), f, lab))
    dropped = []
    keep: dict[str, dict[str, int]] = defaultdict(dict)
    for key, claims in owners.items():
        claims.sort(reverse=True)
        if len(claims) > 1 and claims[0][0] == claims[1][0]:
            dropped.append(claims[0][2])
            continue
        n, f, lab = claims[0]
        keep[f][lab] = n
        dropped += [c[2] + " (" + c[1] + ")" for c in claims[1:]]
    lexicon = {f: sorted(by.items(), key=lambda t: (-t[1], t[0])) for f, by in keep.items()}
    return RulesModel(dict(regions), lexicon, trained_on={
        "source": "fit layouts, familiar split 'train', answer key boxes and printed labels",
        "gold_set_id": gs, "layouts": len(regions), "fields_with_labels": sorted(lexicon),
        "labels_reassigned_or_dropped": sorted(dropped)})


@dataclass
class Extraction:
    field_name: str
    value: str | None
    raw_text: str
    bbox: list[float] | None
    path: str                         # "known_layout" | "label_search"
    signals: dict


def _in(b: list[float], region: list[float]) -> bool:
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    return region[0] <= cx <= region[2] and region[1] <= cy <= region[3]


def _union(ws: list[Word]) -> list[float] | None:
    if not ws:
        return None
    return [min(w.bbox[0] for w in ws), min(w.bbox[1] for w in ws), max(w.bbox[2] for w in ws), max(w.bbox[3] for w in ws)]


def _text(ws: list[Word]) -> str:
    out: dict[str, list[Word]] = {}
    for w in ws:
        out.setdefault(w.line, []).append(w)
    rows = sorted(out.values(), key=lambda g: (min(w.bbox[1] for w in g), min(w.bbox[0] for w in g)))
    return "\n".join(" ".join(w.text for w in sorted(g, key=lambda w: w.bbox[0])) for g in rows)


def extract_known(model: RulesModel, layout_key: str, words: list[Word]) -> list[Extraction]:
    out = []
    for f, reg in sorted(model.regions.get(layout_key, {}).items()):
        ws = [w for w in words if _in(w.bbox, reg["bbox"])]
        raw = _text(ws)
        value = find_value(f, raw)
        out.append(Extraction(f, value, raw, _union(ws), "known_layout", {
            "words": len(ws), "ocr_conf_mean": round(sum(w.conf for w in ws) / len(ws), 1) if ws else None,
            "ocr_conf_min": min((w.conf for w in ws), default=None), "presence_in_layout": reg["presence"],
            "parsed": value is not None}))
    return out


def extract_by_labels(model: RulesModel, words: list[Word]) -> list[Extraction]:
    """Find learned labels in OCR lines and read the value that starts right after them.
    Labels match whole words; a shorter label inside a longer matched one ("TOTAL" in
    "SUB_TOTAL", "Date" in "Due Date") does not count."""
    page = lines(words)
    pats = lexicon_patterns(model.lexicon)
    cands: dict[str, list[tuple]] = defaultdict(list)
    geo = [(min(w.bbox[0] for w in ws), min(w.bbox[1] for w in ws), max(w.bbox[3] for w in ws)) for _, ws in page]

    def below_same_column(li: int) -> str:
        """The next line of the same text block: nearest line below that starts at the same left edge.
        OCR lists lines top to bottom across the page, so in two-column layouts the next line in the
        list is often the other column's ("Total in words: ... fi-" then "SUB_TOTAL : ..." then "fty-four")."""
        x0, _, y1 = geo[li]
        best = None
        for j, (gx0, gy0, _) in enumerate(geo):
            if j != li and abs(gx0 - x0) <= COLUMN_TOLERANCE and gy0 >= y1 - 0.002 and gy0 - y1 <= MAX_LINE_GAP:
                if best is None or gy0 < geo[best][1]:
                    best = j
        if best is None:
            return ""
        t = page[best][0]
        cut = min((m.start() for _, _, _, p in pats for m in p.finditer(t)), default=len(t))
        return t[:cut].strip()

    for li, (text, ws) in enumerate(page):
        hits = label_hits(text, pats)
        follow = below_same_column(li)
        for start, end, f, lab, n_layouts in hits:
            # A value stops where the next label begins: OCR often merges two columns into
            # one line ("BILL_TO: INVOICE ID INV57067424").
            nxt = min((h[0] for h in hits if h[0] >= end), default=len(text))
            rest = text[end:nxt]
            kind = nz.FIELD_TYPES[f]
            body = value_after(f, rest)
            follow_is_value = bool(follow)
            if body is None and kind in ("name", "money_words") and not _LEAD.sub("", rest).strip() and follow_is_value:
                body = follow                                   # "Bill to:" with the name on the next line
            if body is None:
                continue
            if kind == "money_words" and body.rstrip().endswith("-") and follow_is_value:
                body = body + "\n" + follow                    # "twenty-" continues on the next line
            value = find_value(f, body if kind != "money_words" else "words: " + body)
            if value is None and kind == "money_words" and follow_is_value:
                value = find_value(f, "words: " + body + "\n" + follow)
            if value is None:
                continue
            cands[f].append((value, lab, n_layouts, ws, text))
            if f in ("total", "subtotal", "amount_due", "tax"):
                cur = nz.parse_currency(body)
                if cur:
                    cands["currency"].append((cur, lab, n_layouts, ws, text))
    gst = [c for c in cands.get("tax", []) if re.match(r"(?i)gst", c[1]) and re.search(r"(?i)gst", c[4])]
    if len({c[4] for c in gst}) > 1:                    # several GST lines (e.g. 9% + 9%): their sum is the tax
        total_tax = sum(Decimal(c[0]) for c in {c[4]: c for c in gst}.values())
        cands["tax"] = [c for c in cands["tax"] if c not in gst] + [
            (str(total_tax), gst[0][1], gst[0][2], [w for c in gst for w in c[3]], " | ".join(c[4] for c in gst))]
    out = []
    for f, cs in cands.items():
        votes = Counter(c[0] for c in cs)
        # Most candidates wins; on a tie, the value whose label more training layouts used.
        support_of = {v: (n, max(c[2] for c in cs if c[0] == v)) for v, n in votes.items()}
        value = max(support_of, key=lambda v: support_of[v])
        support = votes[value]
        best = max((c for c in cs if c[0] == value), key=lambda c: c[2])
        conf = [w.conf for w in best[3]]
        out.append(Extraction(f, value, best[4], _union(best[3]), "label_search", {
            "label": best[1], "label_layouts": best[2], "candidates": len(cs), "distinct_values": len(votes),
            "agreeing": support, "ocr_conf_mean": round(sum(conf) / len(conf), 1) if conf else None,
            "parsed": True}))
    return out


def extract(model: RulesModel, layout_key: str | None, words: list[Word]) -> list[Extraction]:
    """Known layout: read learned areas; fields the area misses fall back to labels.
    New layout: labels only."""
    by_label = {e.field_name: e for e in extract_by_labels(model, words)}
    if layout_key is None or layout_key not in model.regions:
        return list(by_label.values())
    out = []
    for e in extract_known(model, layout_key, words):
        if e.value is None and e.field_name in by_label:
            fb = by_label.pop(e.field_name)
            fb.signals["fallback_from_known_layout"] = True
            out.append(fb)
        else:
            by_label.pop(e.field_name, None)
            out.append(e)
    return out


def save_model(conn: psycopg.Connection, model: RulesModel) -> int:
    digest = model.content_sha256()
    row = conn.execute("SELECT rules_model_id FROM rules_model WHERE content_sha256 = %s", (digest,)).fetchone()
    if row:
        return row[0]
    return conn.execute(
        "INSERT INTO rules_model (version, content, trained_on, content_sha256) VALUES (%s, %s, %s, %s) RETURNING rules_model_id",
        (model.version, Jsonb({"regions": model.regions, "lexicon": model.lexicon}), Jsonb(model.trained_on), digest)).fetchone()[0]


def load_model(conn: psycopg.Connection, rules_model_id: int | None = None) -> tuple[int, RulesModel]:
    r = conn.execute("""SELECT rules_model_id, version, content, trained_on FROM rules_model
                        WHERE %(id)s::bigint IS NULL OR rules_model_id = %(id)s
                        ORDER BY rules_model_id DESC LIMIT 1""", {"id": rules_model_id}).fetchone()
    if r is None:
        raise LookupError("no rules model; run: make learn-rules")
    c = r[2]
    return r[0], RulesModel(c["regions"], {f: [tuple(x) for x in v] for f, v in c["lexicon"].items()}, r[1], r[3])
