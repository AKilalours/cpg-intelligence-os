"""Tier 2 and 3 extraction: a vision-language model reads the invoice image.

The same prompt and schema serve every provider (Ollama locally, Claude in
the cloud), so tiers differ only in which model answers. The model is asked
to copy values as printed; typing them (dates, exact decimals, currency
codes) is done by cpgos.documents.normalize, the same code as the answer key.
A model is never trusted to do arithmetic or format conversion.
"""

from __future__ import annotations

import difflib
import io
import re

from PIL import Image

from cpgos.documents import normalize as nz
from cpgos.documents.rules import Extraction
from cpgos.llm.base import LLMError, LLMRequest, Provider

PROMPT_VERSION = "invoice-header-1.0.0"

FIELD_HELP = {
    "invoice_number": "the invoice number or id",
    "invoice_date": "the invoice date (not the due date)",
    "due_date": "the payment due date",
    "po_number": "the purchase order (PO) number",
    "seller_name": "the name of the company that issued the invoice (the seller), not the buyer",
    "buyer_name": "the name of the person or company billed (bill to / buyer), first line only",
    "subtotal": "the subtotal amount before discount and tax",
    "discount": "the discount amount (not the percentage)",
    "tax": "the tax amount (not the rate); if several tax lines (e.g. GST), list each amount separated by ' + '",
    "total": "the total amount",
    "amount_due": "the amount due or balance due, only if printed separately from the total",
    "currency": "the currency symbol or code printed with the amounts, e.g. $, USD, EUR",
    "total_in_words": "the total written out in words, exactly as printed",
}

SYSTEM = ("You read business invoices. Copy values exactly as they are printed on the page. "
          "Never calculate, convert, or guess. If a field is not printed, use null.")

PROMPT = ("Read this invoice and return a JSON object with these keys. For each key, give the value "
          "exactly as printed (without its label), or null if it is not on the invoice.\n" +
          "\n".join(f"- {k}: {v}" for k, v in FIELD_HELP.items()))

SCHEMA = {"type": "object", "properties": {k: {"type": ["string", "null"]} for k in FIELD_HELP},
          "required": list(FIELD_HELP), "additionalProperties": False}


def page_png(path, scale: float = 1.0) -> bytes:
    with Image.open(path) as im:
        im = im.convert("RGB")
        if scale != 1.0:
            im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


def _typed(field_name: str, raw) -> str | None:
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw or raw.lower() in ("null", "none", "n/a", "-"):
        return None
    if field_name == "tax" and "+" in raw:
        parts = [nz.parse_amount(p, last=True) for p in raw.split("+")]
        return str(sum(parts)) if parts and all(p is not None for p in parts) else None
    if field_name == "total_in_words":
        return nz.normalize_field(field_name, "words: " + raw)
    return nz.normalize_field(field_name, raw)


def image_id(source_sha256: str, scale: float = 1.0) -> str:
    """Stable identity of a rendered page: same source and rendering = same id on every machine."""
    return f"{source_sha256}@png-x{scale}"


def extract(provider: Provider, image_bytes: bytes, image_id: str | None = None) -> tuple[list[Extraction], dict]:
    """All header fields from one page image, typed. Returns (extractions, call metadata)."""
    req = LLMRequest(system=SYSTEM, prompt=PROMPT, images=[image_bytes], image_ids=[image_id] if image_id else [],
                     json_schema=SCHEMA, max_tokens=700)
    try:
        r = provider.complete(req)
    except LLMError as e:
        return [], {"error": str(e), "client_error": getattr(e, "client_error", False)}
    meta = {"provider": r.provider, "model": r.model, "model_digest": r.model_digest, "seconds": r.seconds,
            "input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "replayed": r.replayed,
            "recording": r.key, "prompt_version": PROMPT_VERSION, "parsed_json": r.data is not None}
    if r.data is None:
        return [], {**meta, "error": "model did not return a JSON object"}
    out = []
    for f in FIELD_HELP:
        raw = r.data.get(f)
        value = _typed(f, raw)
        out.append(Extraction(f, value, str(raw) if raw is not None else "", None, f"vlm:{r.provider}",
                              {"model": r.model, "printed": raw, "typed": value is not None or raw is None}))
    return out, meta


# ---------- grounding: a model's value counts only if it can be found on the page ----------

def _norm_tok(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower())


def ground(field_name: str, printed: str | None, value: str | None, words) -> tuple[bool, list[float] | None, str]:
    """Find a model-read value in the page's OCR words. Returns (found, box of the matching words, how).

    Money and dates are compared as typed values (an OCR token that types to the same amount or
    date); ids exactly or within one OCR slip; names and words as a close match of a run of words.
    A value that is nowhere on the page (a computed total, an invented number) is not grounded."""
    from cpgos.documents.ocr import lines
    from cpgos.documents.rules import _DATE_PATTERNS, _union
    if value is None:
        return False, None, "no value"
    kind = nz.FIELD_TYPES[field_name]
    page = lines(words)
    if kind == "currency":
        for text, ws in page:
            if nz.parse_currency(text) == value:
                return True, _union(ws), "currency on page"
        return False, None, "currency not on page"
    if kind == "money" and field_name != "tax":
        for w in words:
            for tok in re.findall(r"\d[\d,.]*\d|\d", w.text):
                if nz.parse_amount(tok, last=True) is not None and str(nz.parse_amount(tok, last=True)) == value:
                    return True, w.bbox, "amount on page"
        return False, None, "amount not on page"
    if field_name == "tax":
        parts = [p for p in re.split(r"\s*\+\s*", printed or "") if p] or [value]
        boxes = []
        for part in parts:
            want = nz.parse_amount(part, last=True)
            hit = next((w for w in words for tok in re.findall(r"\d[\d,.]*\d|\d", w.text)
                        if want is not None and nz.parse_amount(tok, last=True) == want), None)
            if hit is None:
                return False, None, "tax amount not on page"
            boxes.append(hit)
        return True, _union(boxes), "every tax amount on page"
    if kind == "date":
        for text, ws in page:
            for pat in _DATE_PATTERNS:
                for m in re.finditer(pat, text):
                    if nz.normalize_field(field_name, m.group(0)) == value:
                        return True, _union(ws), "date on page"
        return False, None, "date not on page"
    if kind == "id":
        want = _norm_tok(value)
        for w in words:
            got = _norm_tok(w.text)
            if got.endswith(want) or (len(want) > 4 and difflib.SequenceMatcher(None, got[-len(want):], want).ratio() >= 0.9):
                return True, w.bbox, "id on page"
        return False, None, "id not on page"
    # names, free text, amounts in words: a run of words that closely matches
    target = " ".join(_norm_tok(t) for t in (printed or value).split() if _norm_tok(t))
    n = max(1, len(target.split()))
    best, best_ws = 0.0, None
    for _, ws in page:
        toks = [_norm_tok(w.text) for w in ws]
        for i in range(len(ws)):
            for j in range(i + max(1, n - 2), min(len(ws), i + n + 2) + 1):
                r = difflib.SequenceMatcher(None, " ".join(t for t in toks[i:j] if t), target).ratio()
                if r > best:
                    best, best_ws = r, ws[i:j]
    if kind == "money_words":
        # Amounts in words wrap across lines and columns: compare the letters in order over the
        # whole page, allowing breaks, and require most of the printed letters to be found.
        want = re.sub(r"[^a-z]", "", (printed or "").lower())
        have = re.sub(r"[^a-z]", "", " ".join(t for t, _ in page).lower())
        found = sum(b.size for b in difflib.SequenceMatcher(None, have, want, autojunk=False).get_matching_blocks())
        cover = found / len(want) if want else 0.0
        return cover >= 0.9, _union(best_ws) if best_ws else None, f"letters found {cover:.2f}"
    return (best >= 0.85, _union(best_ws) if best >= 0.85 else None, f"text match {best:.2f}")


def role_conflict(field_name: str, box: list[float] | None, words, lexicon: dict | None) -> str | None:
    """The other field whose printed label introduces the line where a value was found, if any.
    A subtotal read from "TOTAL : 810.70", or a buyer read from the seller's "Address:" line,
    is the right text in the wrong role."""
    if not lexicon or box is None:
        return None
    from cpgos.documents.ocr import lines
    from cpgos.documents.rules import label_hits, lexicon_patterns
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    for text, ws in lines(words):
        if not any(w.bbox[0] <= cx <= w.bbox[2] and w.bbox[1] <= cy <= w.bbox[3] for w in ws):
            continue
        # position of the value's first word in the line text
        start, pos = 0, None
        for w in ws:
            i = text.find(w.text, start)
            if w.bbox[0] <= cx <= w.bbox[2] or w.bbox[0] >= box[0] - 1e-6:
                pos = i
                break
            start = i + len(w.text)
        pos = len(text) if pos is None else pos
        before = [(h[1], h[2]) for h in label_hits(text, lexicon_patterns(lexicon)) if h[1] <= pos + 1]
        if before:
            owner = max(before)[1]
            if owner != field_name and not (field_name == "currency"):
                return owner
        return None
    return None


def _fuzzy_label(text: str, labels) -> list:
    """The field's label printed with an OCR slip ("T0TAL" for "TOTAL"): a run of the line's letters
    and digits at least 80% similar to a label of 4+ characters. Exact matches are found earlier."""
    sq = re.sub(r"[^a-z0-9]", "", text.lower())
    for lab, _ in labels:
        want = re.sub(r"[^a-z0-9]", "", lab.lower())
        if len(want) < 4:
            continue
        for n in (len(want) - 1, len(want), len(want) + 1):
            for i in range(0, max(0, len(sq) - n) + 1):
                if difflib.SequenceMatcher(None, sq[i:i + n], want).ratio() >= 0.8:
                    return [lab]
    return []


def own_label_near(field_name: str, box: list[float] | None, words, lexicon: dict | None) -> bool:
    """Positive evidence of role: one of the field's own learned labels is printed on the value's
    line before it, or on the line just above in the same column (e.g. "BILL_TO:" over a name)."""
    if not lexicon or box is None or field_name not in lexicon:
        return False
    from cpgos.documents.ocr import lines
    from cpgos.documents.rules import COLUMN_TOLERANCE, MAX_LINE_GAP, label_hits, lexicon_patterns
    pats = lexicon_patterns(lexicon)
    for text, ws in lines(words):
        x0, y0, x1, y1 = min(w.bbox[0] for w in ws), min(w.bbox[1] for w in ws), max(w.bbox[2] for w in ws), max(w.bbox[3] for w in ws)
        own = [h for h in label_hits(text, pats) if h[2] == field_name] or _fuzzy_label(text, lexicon[field_name])
        if not own:
            continue
        same_line = y0 - 0.004 <= (box[1] + box[3]) / 2 <= y1 + 0.004 and x0 <= box[0] + 1e-6
        above = abs(x0 - box[0]) <= COLUMN_TOLERANCE * 3 and 0 <= box[1] - y1 <= MAX_LINE_GAP
        if same_line or above:
            return True
    return False


def plausible(field_name: str, printed: str | None, value: str | None) -> str | None:
    """Reasons a model value cannot be a faithful copy, or None."""
    p = (printed or "").strip()
    if field_name != "tax" and "+" in p:
        return "contains a sum: the model added numbers instead of copying one"
    if nz.FIELD_TYPES[field_name] in ("name", "text"):
        if len(re.findall(r"[A-Za-z]", value or "")) < 3 or re.match(r"\s*\d", value or ""):
            return "not a plausible name"
    return None


def grounded(exs: list[Extraction], words, lexicon: dict | None = None) -> list[Extraction]:
    """Keep only values that are a faithful copy, found on the page, in the right role.

    Version 2 (designed on calibration invoices): a field that normally carries a printed label
    must have its own label next to the value (positive evidence), not merely no conflicting one;
    a value containing a sum is refused; names must look like names."""
    out = []
    for e in exs:
        bad = plausible(e.field_name, e.signals.get("printed"), e.value) if e.value is not None else None
        if bad:
            ok, box, how = False, None, bad
        else:
            ok, box, how = ground(e.field_name, e.signals.get("printed"), e.value, words)
        if ok and nz.FIELD_TYPES[e.field_name] in ("money", "name", "text", "date", "id"):
            other = role_conflict(e.field_name, box, words, lexicon)
            if other:
                ok, how = False, f"found on a line labelled {other}"
            elif lexicon and e.field_name in lexicon and not own_label_near(e.field_name, box, words, lexicon):
                ok, how = False, "no label for this field next to the value"
        sig = {**e.signals, "grounded": ok, "grounding": how}
        out.append(Extraction(e.field_name, e.value if ok else None, e.raw_text, box, e.path, sig))
    return out
