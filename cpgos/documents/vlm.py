"""Tier 2 and 3 extraction: a vision-language model reads the invoice image.

The same prompt and schema serve every provider (Ollama locally, Claude in
the cloud), so tiers differ only in which model answers. The model is asked
to copy values as printed; typing them (dates, exact decimals, currency
codes) is done by cpgos.documents.normalize, the same code as the answer key.
A model is never trusted to do arithmetic or format conversion.
"""

from __future__ import annotations

import io

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


def extract(provider: Provider, image_bytes: bytes) -> tuple[list[Extraction], dict]:
    """All header fields from one page image, typed. Returns (extractions, call metadata)."""
    req = LLMRequest(system=SYSTEM, prompt=PROMPT, images=[image_bytes], json_schema=SCHEMA, max_tokens=700)
    try:
        r = provider.complete(req)
    except LLMError as e:
        return [], {"error": str(e)}
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
