"""Reading receipt photos (CORD): line items and totals, checked by arithmetic.

Analogy: a cashier's drawer count. Two people reading the same receipt can
both misread a smudged digit, but if the lines add up to the subtotal and the
subtotal, tax, and service add up to the total, a misread almost always
breaks the sum. Arithmetic is a witness that does not share a model's blind
spots, so it decides which readings may skip a person.

Readers:
- local model (Ollama qwen2.5vl:7b on her Mac): reads every receipt;
- Claude: called only for receipts whose local reading does not add up.

The rule that decides what goes straight through is chosen on the validation
split (`POLICIES`), frozen in `FROZEN_POLICY`, and only then scored once on
test. Scoring uses the CORD answer key in eval.*, which the engine cannot read;
arithmetic claims are also reported on the receipts whose own labels add up
(the label audit), so a reader is never blamed for an error in the key.
"""

from __future__ import annotations

import io
import re
from collections import Counter, defaultdict
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path

import psycopg
from PIL import Image
from psycopg.types.json import Jsonb

from cpgos.documents import cord
from cpgos.llm.base import LLMError, LLMRequest

PROMPT_VERSION = "receipt-1.0.0"
MAX_SIDE = 1280
TOTALS = ("subtotal", "discount", "tax", "service", "total", "cash", "change", "card")
UNSIGNED = ("discount", "change")         # printed with or without a minus sign; compared as amounts
NAME_MATCH = 0.8                          # item names: similarity of letters and digits (OCR-style slips allowed)
POLICIES = ("local_alone", "local_then_claude", "local_then_claude_or_agreement")
FROZEN_POLICY: str | None = None          # set from the validation results, before test is scored

SYSTEM = ("You read shop receipts photographed in Indonesia. Copy every amount exactly as printed, keeping its "
          "dots and commas (for example 60.000 or 45,000.00). Never compute or guess a value that is not printed.")
PROMPT = """Read this receipt and return a JSON object with:
- "items": every purchased line in printed order, each {"name", "quantity", "unit_price", "price"}. "price" is the
  line amount printed for that line; use null for anything not printed on that line. Add-ons printed under a line
  (toppings, sizes) belong to that line: do not list them as separate items.
- "subtotal", "discount", "tax", "service", "total", "cash", "change", "card": the amount printed on that line of the
  receipt, or null if the receipt has no such line. "card" is any card or e-wallet payment."""
_AMOUNT = {"type": ["string", "number", "null"]}
SCHEMA = {"type": "object",
          "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
              "name": {"type": ["string", "null"]}, "quantity": _AMOUNT, "unit_price": _AMOUNT, "price": _AMOUNT},
              "required": ["name", "quantity", "unit_price", "price"]}},
              **{k: _AMOUNT for k in TOTALS}},
          "required": ["items", *TOTALS]}


# ---------- one reading ----------

def receipt_png(path: Path) -> bytes:
    """The photo, longest side at most MAX_SIDE pixels (enough for receipt print; bounds model cost)."""
    with Image.open(path) as im:
        im = im.convert("RGB")
        scale = MAX_SIDE / max(im.size)
        if scale < 1:
            im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


def image_id(sha256: str) -> str:
    return f"{sha256}@png-max{MAX_SIDE}"


def _amount(v) -> str | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        v = format(Decimal(str(v)).normalize(), "f")
    return cord.canonical(cord.parse_idr(str(v)))


def parse(data: dict) -> dict:
    """A model's JSON -> {"fields": {name: canonical}, "items": [...], "printed": data}."""
    items = []
    for it in data.get("items") or []:
        if not isinstance(it, dict):
            continue
        q = it.get("quantity")
        items.append({"name": " ".join(str(it.get("name") or "").split()) or None,
                      "quantity": cord.canonical(cord.parse_count(str(q))) if q is not None else None,
                      "unit_price": _amount(it.get("unit_price")), "price": _amount(it.get("price"))})
    fields = {}
    for f in TOTALS:
        v = _amount(data.get(f))
        if v is not None and f in UNSIGNED:
            v = v.lstrip("-")
        fields[f] = v
    return {"fields": fields, "items": items, "printed": data}


def read(provider, png: bytes, iid: str) -> tuple[dict | None, dict]:
    req = LLMRequest(system=SYSTEM, prompt=PROMPT, images=[png], image_ids=[iid], json_schema=SCHEMA, max_tokens=1500)
    try:
        r = provider.complete(req)
    except LLMError as e:
        return None, {"error": str(e), "client_error": getattr(e, "client_error", False)}
    meta = {"provider": r.provider, "model": r.model, "seconds": r.seconds, "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens, "replayed": r.replayed, "recording": r.key}
    if not isinstance(r.data, dict):
        return None, {**meta, "error": "model did not return a JSON object"}
    return parse(r.data), meta


def to_receipt(body: dict) -> cord.Receipt:
    """A reading (or the answer key in the same shape) as a cord.Receipt, so the label audit can check it."""
    r = cord.Receipt(doc_key="", split="", image_id=0, width=0, height=0)
    for f, v in body["fields"].items():
        if v is not None:
            r.fields[f] = (v, Decimal(v), None)
    for it in body["items"]:
        d = lambda k: Decimal(it[k]) if it.get(k) is not None else None   # noqa: E731
        r.items.append(cord.Item(it.get("name"), d("quantity"), d("unit_price"), d("price"), d("discount"),
                                 it.get("sub_items") or [], {}))
    return r


def arithmetic(body: dict | None) -> tuple[bool, dict]:
    """Does a reading add up? Needs a total, at least one applicable check, and no failing check."""
    if body is None:
        return False, {"reason": "no reading"}
    checks = cord.audit(to_receipt(body))
    decided = {k: v for k, v in checks.items() if v is not None}
    ok = body["fields"].get("total") is not None and bool(decided) and "fails" not in decided.values()
    return ok, checks


# ---------- running the readers ----------

def version(provider) -> str:
    digest = (provider.digest() or "unknown")[-12:] if provider.name == "ollama" else provider.model
    return f"{PROMPT_VERSION}+{provider.name}:{provider.model}@{digest}+max{MAX_SIDE}"


def split_documents(conn, split: str) -> list[tuple[int, str, str]]:
    return conn.execute(
        """SELECT s.source_document_id, s.storage_key, s.sha256 FROM eval.receipt_meta m
           JOIN source_document s USING (source_document_id) WHERE m.split = %s ORDER BY m.doc_key""",
        (split,)).fetchall()


def run_reader(conn, raw_root: Path, docs, provider, reader: str, progress=print, max_same_errors: int = 3) -> dict:
    """Read each receipt not yet read under this version; store as the engine role. Resumable."""
    ver = version(provider)
    done = {sid for (sid,) in conn.execute("SELECT source_document_id FROM receipt_extraction WHERE extractor_version = %s "
                                           "AND body IS NOT NULL", (ver,))}
    todo = [d for d in docs if d[0] not in done]
    stats = Counter()
    secs, last_errors = [], []
    for i, (sid, key, sha) in enumerate(todo, 1):
        body, meta = read(provider, receipt_png(raw_root / key), image_id(sha))
        if body is None:
            stats["failed"] += 1
            last_errors = (last_errors + [meta.get("error", "")[:80]])[-max_same_errors:]
            progress(f"  {i}/{len(todo)} FAILED: {meta.get('error', '')[:120]}")
            if meta.get("client_error") and len(last_errors) == max_same_errors and len(set(last_errors)) == 1:
                raise LLMError(f"stopped after {max_same_errors} identical rejections: {last_errors[0]}")
            continue
        last_errors = []
        with conn.transaction():
            conn.execute("SET LOCAL ROLE cpgos_engine")
            conn.execute("""INSERT INTO receipt_extraction (source_document_id, extractor_version, reader, body, call)
                            VALUES (%s, %s, %s, %s, %s) ON CONFLICT (source_document_id, extractor_version) DO NOTHING""",
                         (sid, ver, reader, Jsonb(body), Jsonb(meta)))
        stats["read"] += 1
        stats["replayed"] += bool(meta.get("replayed"))
        secs.append(meta.get("seconds") or 0)
        progress(f"  {i}/{len(todo)} {meta.get('seconds')} s{' (replayed)' if meta.get('replayed') else ''}")
    return {"extractor_version": ver, "receipts": len(docs), "to_read": len(todo), **stats,
            "seconds_per_receipt": round(sum(secs) / len(secs), 1) if secs else None}


def readings(conn, ver: str, ids: list[int]) -> dict[int, dict]:
    return {sid: b for sid, b in conn.execute(
        "SELECT source_document_id, body FROM receipt_extraction WHERE extractor_version = %s "
        "AND source_document_id = ANY(%s) AND body IS NOT NULL", (ver, ids))}


# ---------- the answer key, in the same shape ----------

def gold(conn, ids: list[int]) -> dict[int, dict]:
    gs = conn.execute("SELECT max(gold_set_id) FROM eval.gold_set WHERE name = %s", (cord.GOLD_NAME,)).fetchone()[0]
    out = {sid: {"fields": {}, "items": []} for sid in ids}
    for sid, f, v in conn.execute("SELECT source_document_id, field_name, value FROM eval.receipt_gold WHERE gold_set_id = %s "
                                  "AND source_document_id = ANY(%s) AND field_name = ANY(%s)", (gs, ids, list(TOTALS))):
        out[sid]["fields"][f] = v.lstrip("-") if (v is not None and f in UNSIGNED) else v
    for sid, name, q, u, p, d, subs in conn.execute(
            """SELECT source_document_id, name, quantity, unit_price, price, discount, sub_items FROM eval.receipt_item_gold
               WHERE gold_set_id = %s AND source_document_id = ANY(%s) ORDER BY source_document_id, item_index""", (gs, ids)):
        c = lambda x: cord.canonical(Decimal(x)) if x is not None else None   # noqa: E731
        out[sid]["items"].append({"name": name, "quantity": c(q), "unit_price": c(u), "price": c(p), "discount": c(d),
                                  "sub_items": subs})
    return out


# ---------- scoring ----------

def _name(s: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def name_similarity(a: str | None, b: str | None) -> float:
    a, b = _name(a), _name(b)
    return 1.0 if a == b else SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def match_items(gold_items: list[dict], pred_items: list[dict]) -> list[tuple[dict, dict | None]]:
    """One-to-one: for each gold line, the unused predicted line with the same price and the closest name,
    else the closest name. Greedy in printed order (receipts are read top to bottom)."""
    free = list(range(len(pred_items)))
    out = []
    for g in gold_items:
        best, best_key = None, None
        for j in free:
            p = pred_items[j]
            key = (p.get("price") == g.get("price"), name_similarity(g.get("name"), p.get("name")))
            if best_key is None or key > best_key:
                best, best_key = j, key
        if best is not None and (best_key[0] or best_key[1] >= NAME_MATCH):
            free.remove(best)
            out.append((g, pred_items[best]))
        else:
            out.append((g, None))
    return out


def item_correct(g: dict, p: dict | None) -> bool:
    return (p is not None and name_similarity(g.get("name"), p.get("name")) >= NAME_MATCH and p.get("price") == g.get("price")
            and (g.get("quantity") is None or p.get("quantity") == g.get("quantity")))


def score_receipt(g: dict, p: dict | None) -> dict:
    p = p or {"fields": {}, "items": []}
    out = Counter()
    for f in TOTALS:
        gv, pv = g["fields"].get(f), p["fields"].get(f)
        out[("correct" if gv == pv else "wrong" if gv is not None and pv is not None else
             "missed" if gv is not None else "spurious") if (gv is not None or pv is not None) else "both_blank"] += 1
    pairs = match_items(g["items"], p["items"])
    out["items_gold"] = len(g["items"])
    out["items_read"] = len(p["items"])
    out["items_correct"] = sum(item_correct(a, b) for a, b in pairs)
    amounts_ok = out["wrong"] == out["missed"] == out["spurious"] == 0 and \
        sorted(str(i.get("price")) for i in g["items"]) == sorted(str(i.get("price")) for i in p["items"])
    out["amounts_exact"] = int(amounts_ok)
    out["fully_exact"] = int(amounts_ok and out["items_correct"] == len(g["items"]) == len(p["items"]))
    return out


def score(golds: dict[int, dict], preds: dict[int, dict | None]) -> dict:
    from cpgos.finance.evaluate import rate
    t = Counter()
    for sid, g in golds.items():
        t.update(score_receipt(g, preds.get(sid)))
    fields_p = t["correct"] + t["wrong"] + t["spurious"]
    fields_g = t["correct"] + t["wrong"] + t["missed"]
    ip, ir = rate(t["items_correct"], t["items_read"]), rate(t["items_correct"], t["items_gold"])
    f1 = lambda p, r: round(2 * p * r / (p + r), 4) if p and r else None   # noqa: E731
    return {"receipts": len(golds), "read": sum(preds.get(s) is not None for s in golds),
            "totals": {"precision": rate(t["correct"], fields_p), "recall": rate(t["correct"], fields_g),
                       "f1": f1(rate(t["correct"], fields_p)["rate"], rate(t["correct"], fields_g)["rate"]),
                       "counts": {k: t[k] for k in ("correct", "wrong", "missed", "spurious")}},
            "items": {"precision": ip, "recall": ir, "f1": f1(ip["rate"], ir["rate"])},
            "amounts_exact": rate(t["amounts_exact"], len(golds)), "fully_exact": rate(t["fully_exact"], len(golds))}


# ---------- what may skip a person ----------

def _agree(a: dict | None, b: dict | None) -> bool:
    if a is None or b is None or a["fields"].get("total") is None:
        return False
    return a["fields"].get("total") == b["fields"].get("total") and \
        sorted(str(i.get("price")) for i in a["items"]) == sorted(str(i.get("price")) for i in b["items"])


def decide(policy: str, local: dict | None, claude: dict | None) -> tuple[dict | None, str]:
    """(accepted reading or None, reason). None means a person reviews the receipt."""
    ok_local, _ = arithmetic(local)
    if ok_local:
        return local, "local reading adds up"
    if policy == "local_alone":
        return None, "local reading does not add up"
    ok_claude, _ = arithmetic(claude)
    if ok_claude:
        return claude, "Claude's reading adds up"
    if policy == "local_then_claude_or_agreement" and _agree(local, claude):
        return claude, "two readers agree on the total and every line amount"
    return None, "no reading adds up" if claude is not None else "no second reading"


def needs_claude(local: dict | None) -> bool:
    return not arithmetic(local)[0]


def evaluate_policies(golds: dict[int, dict], local: dict[int, dict], claude: dict[int, dict],
                      policies=POLICIES, consistent: set[int] | None = None) -> dict:
    """Straight-through rate and the correctness of what went straight through, per policy."""
    from cpgos.finance.evaluate import rate
    out = {}
    for pol in policies:
        acc, reasons, wrong = {}, Counter(), []
        for sid in golds:
            body, why = decide(pol, local.get(sid), claude.get(sid))
            reasons[why] += 1
            if body is not None:
                acc[sid] = body
        s = {sid: score_receipt(golds[sid], b) for sid, b in acc.items()}
        wrong = [sid for sid, x in s.items() if not x["amounts_exact"]]
        out[pol] = {"straight_through": rate(len(acc), len(golds)),
                    "accepted_amounts_exact": rate(sum(x["amounts_exact"] for x in s.values()), len(s)),
                    "accepted_fully_exact": rate(sum(x["fully_exact"] for x in s.values()), len(s)),
                    "accepted_amounts_exact_where_key_adds_up": rate(
                        sum(x["amounts_exact"] for sid, x in s.items() if consistent is None or sid in consistent),
                        sum(1 for sid in s if consistent is None or sid in consistent)),
                    "reasons": dict(reasons), "accepted_but_wrong": wrong}
    return out
