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
from cpgos.llm.base import LLMError, LLMRequest, parse_json

PROMPT_VERSION = "receipt-2.0.0"
# 2.0: the model copies every totals line as printed (label and amount) and code assigns the field from the
#      label, using label words learned from train. 1.0 asked the model for "the subtotal", and it invented one
#      (equal to the total) on 33 of 100 validation receipts that print none (report 16).
# read-1.1: amounts are read from the model's literal text. 1.0 read JSON numbers as floats, so "89.100"
#      (89,100 rupiah) became 89.1; whole receipts shrank 1,000 times and still added up (report 16).
READING_VERSION = "read-1.2"   # 1.2: counts recognised first, cut-off labels match, unexplained-amount guard
MAX_SIDE = 1280
TOTALS = ("subtotal", "discount", "tax", "service", "total", "cash", "change", "card")
UNSIGNED = ("discount", "change")         # printed with or without a minus sign; compared as amounts
NAME_MATCH = 0.8                          # item names: similarity of letters and digits (OCR-style slips allowed)
POLICIES = ("local_alone", "local_then_claude", "local_then_claude_or_agreement")
# Frozen from validation (reports 17 and 331), before test was read: every reading that skips a person must
# add up, account for every printed amount, and look like rupiah, whichever model read it. The agreement rule
# was rejected because it accepts readings whose sums do not close (validation 0094: "OTHERS" read as a card).
FROZEN_POLICY: str | None = "local_then_claude"

SYSTEM = ("You transcribe shop receipts photographed in Indonesia. Copy text and amounts exactly as printed, keeping "
          "the dots and commas in amounts (for example 60.000 or 45,000.00). Never compute, infer, or repeat a value "
          "that is not printed.")
PROMPT = """Transcribe this receipt as a JSON object with:
- "items": every purchased line in printed order, each {"name", "quantity", "unit_price", "price"}. "price" is the
  line amount printed for that line; use null for anything not printed on that line. Add-ons printed under a line
  (toppings, sizes) belong to that line: do not list them as separate items.
- "totals": every line printed below the items that has an amount (subtotal, discount, tax, service, total,
  payment, change, and any other), in printed order, each {"label": the words printed on that line, exactly,
  "amount": the amount printed on that line, exactly}. Only lines that are actually printed."""
_TEXT = {"type": ["string", "null"]}
SCHEMA = {"type": "object",
          "properties": {
              "items": {"type": "array", "items": {"type": "object", "properties": {
                  "name": _TEXT, "quantity": _TEXT, "unit_price": _TEXT, "price": _TEXT},
                  "required": ["name", "quantity", "unit_price", "price"]}},
              "totals": {"type": "array", "items": {"type": "object", "properties": {"label": _TEXT, "amount": _TEXT},
                                                    "required": ["label", "amount"]}}},
          "required": ["items", "totals"]}


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
    if isinstance(v, float):
        raise TypeError("receipt amounts must be the model's literal text; parse with literal_numbers=True")
    return cord.canonical(cord.parse_idr(str(v)))


def parse(data: dict, labels: dict) -> dict:
    """A model's JSON -> {"fields": {name: canonical}, "items": [...], "totals_lines": [...], "printed": data}.
    Each totals line's field is decided by its printed label; a field printed twice with different amounts,
    or a label no train receipt used, gives no value (a person or the second reader decides)."""
    items = []
    for it in data.get("items") or []:
        if not isinstance(it, dict):
            continue
        q = it.get("quantity")
        items.append({"name": " ".join(str(it.get("name") or "").split()) or None,
                      "quantity": cord.canonical(cord.parse_count(str(q))) if q is not None else None,
                      "unit_price": _amount(it.get("unit_price")), "price": _amount(it.get("price"))})
    lines = []
    for t in data.get("totals") or []:
        if isinstance(t, dict):
            ln = {"label": str(t.get("label") or ""), "amount": _amount(t.get("amount"))}
            # "TOTAL QTY 3" names a count, not the total: counts are recognised before labels are classified.
            ln["field"] = None if _count_only(ln) else classify(ln["label"], labels)
            lines.append(ln)
    fields, conflicts = {}, []
    for f in TOTALS:
        amounts = {ln["amount"] for ln in lines if ln["field"] == f and ln["amount"] is not None}
        if len(amounts) > 1:
            conflicts.append(f)
        v = amounts.pop() if len(amounts) == 1 else None
        fields[f] = v.lstrip("-") if (v is not None and f in UNSIGNED) else v
    return {"fields": fields, "items": items, "totals_lines": lines, "conflicts": conflicts, "printed": data}


def read(provider, png: bytes, iid: str, labels: dict) -> tuple[dict | None, dict]:
    req = LLMRequest(system=SYSTEM, prompt=PROMPT, images=[png], image_ids=[iid], json_schema=SCHEMA, max_tokens=1500)
    try:
        r = provider.complete(req)
    except LLMError as e:
        return None, {"error": str(e), "client_error": getattr(e, "client_error", False)}
    meta = {"provider": r.provider, "model": r.model, "seconds": r.seconds, "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens, "replayed": r.replayed, "recording": r.key}
    data = parse_json(r.text, literal_numbers=True)          # never r.data: floats lose the printed digits
    if not isinstance(data, dict):
        return None, {**meta, "error": "model did not return a JSON object"}
    return parse(data, labels), meta


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


UNEXPLAINED_GUARD = True       # an amount nobody can account for sends the receipt to a person (validation report 17)


def _count_only(line: dict) -> bool:
    """'TOTAL QTY 3', 'ITEMS: 2': a printed count, not money (amount equals the number in the label, or is small)."""
    nums = re.findall(r"\d+", line["label"] or "")
    return line["amount"] is not None and (line["amount"] in nums or Decimal(0) < Decimal(line["amount"]) < 100)


MIN_TOTAL = Decimal(1000)        # no receipt in CORD totals under Rp 1,000; the smallest coin in use is Rp 100


def arithmetic(body: dict | None) -> tuple[bool, dict]:
    """Does a reading add up? Needs a plausible rupiah total, at least one applicable check, and no failing check.

    Sums cannot see a scale error (every amount 1,000 times too small still adds up), so amounts must
    also look like rupiah: whole numbers, and a total of at least Rp 1,000."""
    if body is None:
        return False, {"reason": "no reading"}
    amounts = [Decimal(v) for v in body["fields"].values() if v is not None] + \
              [Decimal(i[k]) for i in body["items"] for k in ("unit_price", "price") if i.get(k) is not None]
    if any(a != a.to_integral_value() for a in amounts):
        return False, {"reason": "an amount has a fractional part, which rupiah receipts do not print"}
    total = body["fields"].get("total")
    if total is not None and Decimal(total) < MIN_TOTAL:
        return False, {"reason": "total under Rp 1,000"}
    unexplained = [ln["label"] for ln in body.get("totals_lines", [])
                   if ln["field"] is None and ln["amount"] not in (None, "0") and not _count_only(ln)]
    if UNEXPLAINED_GUARD and unexplained:
        return False, {"reason": "an amount in the totals block has a label no rule understands", "labels": unexplained}
    if body.get("conflicts"):
        return False, {"reason": "a field is printed twice with different amounts", "fields": body["conflicts"]}
    checks = cord.audit(to_receipt(body))
    decided = {k: v for k, v in checks.items() if v is not None}
    ok = body["fields"].get("total") is not None and bool(decided) and "fails" not in decided.values()
    return ok, checks


# ---------- which field a printed totals label names ----------
# Learned from train labels (CORD marks label words). Held-out check on validation's own labels: 361 of 373
# printed labels map to the right field; the misses are rare spellings ("Srv Ch"), which give no value.
# (Checking labels with OCR was tried and dropped: Tesseract finds no words at all on some clear photos.)

LABELS_VERSION = "labels-1.0.0"
LABEL_MIN_COUNT = 2
_CATEGORY = {f"{block}.{key}": name for (block, key), name in cord.TOTAL_FIELDS.items() if not name.endswith("_count")}
_CATEGORY["total.emoneyprice"] = "card"           # "card" = any card or e-wallet payment, as the prompt says
LABELS_FILE = Path(__file__).resolve().parents[2] / "data" / "processed" / "cord" / f"receipt_{LABELS_VERSION}.json"


def norm_label(text: str) -> str:
    """Upper case, punctuation dropped, tokens without letters dropped ("TAX 10%:" -> "TAX")."""
    toks = re.sub(r"[^A-Z0-9%]+", " ", text.upper()).split()
    return " ".join(t for t in toks if re.search(r"[A-Z]", t))


def learn_labels(records) -> dict:
    """Printed label words per field, from TRAIN receipts only (CORD marks label words with is_key).
    A label belongs to the one field it labels most often, so "TOTAL" is a total label even though
    22 train receipts print it beside a subtotal."""
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for _, gt in records:
        if cord.SPLITS.get(gt.get("meta", {}).get("split")) != "train":
            continue
        for line in gt.get("valid_line", []):
            field = _CATEGORY.get(line.get("category"))
            label = norm_label(" ".join(w["text"] for w in line.get("words", []) if w.get("is_key")))
            if field and label:
                counts[label][field] += 1
    out: dict[str, list[str]] = defaultdict(list)
    reassigned = 0
    for label, by_field in counts.items():
        field, n = max(by_field.items(), key=lambda kv: (kv[1], kv[0]))
        reassigned += len(by_field) > 1
        if n >= LABEL_MIN_COUNT:
            out[field].append(label)
    return {"version": LABELS_VERSION, "labels": {f: sorted(v) for f, v in sorted(out.items())},
            "labels_claimed_by_several_fields": reassigned}


def load_labels(raw_root: Path, path: Path = LABELS_FILE) -> dict:
    """Learned once from the train split and cached; the same Parquet files give the same labels everywhere."""
    import json
    if path.is_file():
        return json.loads(path.read_text())
    lab = learn_labels(cord.read_records(raw_root / "cord" / "data"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(lab, indent=1))
    return lab


PREFIX_MIN = 5          # a printed word cut short or inflected still matches: "KEMBAL" ~ "KEMBALI", "CHANGES" ~ "CHANGE"


def _word_match(a: str, b: str) -> bool:
    return a == b or (min(len(a), len(b)) >= PREFIX_MIN and (a.startswith(b) or b.startswith(a)))


def _contains(words: list[str], lab: list[str]) -> bool:
    n = len(lab)
    return any(all(_word_match(words[i + k], lab[k]) for k in range(n)) for i in range(len(words) - n + 1))


def classify(label: str, labels: dict) -> str | None:
    """The field a printed label names: an exact learned label, else the longest learned label inside it
    (whole words, cut-off words allowed), so "SUBTOTAL" is never "TOTAL" and "TOTAL KEMBALIAN" is change.
    One field per line; a tie between fields gives no field."""
    t = norm_label(label)
    if not t:
        return None
    words = t.split()
    hits = []
    for f, labs in labels["labels"].items():
        for lab in labs:
            if lab == t:
                return f
            if _contains(words, lab.split()):
                hits.append((len(lab), f))
    if not hits:
        return None
    top = max(n for n, _ in hits)
    fields = {f for n, f in hits if n == top}
    return fields.pop() if len(fields) == 1 else None


# ---------- running the readers ----------

def version(provider) -> str:
    digest = (provider.digest() or "unknown")[-12:] if provider.name == "ollama" else provider.model
    return f"{PROMPT_VERSION}/{READING_VERSION}/{LABELS_VERSION}+{provider.name}:{provider.model}@{digest}+max{MAX_SIDE}"


def split_documents(conn, split: str) -> list[tuple[int, str, str]]:
    return conn.execute(
        """SELECT s.source_document_id, s.storage_key, s.sha256 FROM eval.receipt_meta m
           JOIN source_document s USING (source_document_id) WHERE m.split = %s ORDER BY m.doc_key""",
        (split,)).fetchall()


def run_reader(conn, raw_root: Path, docs, provider, reader: str, labels: dict, progress=print,
               max_same_errors: int = 3) -> dict:
    """Read each receipt not yet read under this version; store as the engine role. Resumable."""
    ver = version(provider)
    done = {sid for (sid,) in conn.execute("SELECT source_document_id FROM receipt_extraction WHERE extractor_version = %s "
                                           "AND body IS NOT NULL", (ver,))}
    todo = [d for d in docs if d[0] not in done]
    stats = Counter()
    secs, last_errors = [], []
    for i, (sid, key, sha) in enumerate(todo, 1):
        body, meta = read(provider, receipt_png(raw_root / key), image_id(sha), labels)
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
    for sid, v in conn.execute("SELECT source_document_id, value FROM eval.receipt_gold WHERE gold_set_id = %s "
                               "AND source_document_id = ANY(%s) AND field_name = 'emoney'", (gs, ids)):
        if out[sid]["fields"].get("card") is None:
            out[sid]["fields"]["card"] = v                     # "card" = any card or e-wallet payment
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


ZERO_IS_ABSENT = ("discount", "tax", "service")   # a printed "Discount 0" and no discount line mean the same money


def _money_lines(items: list[dict]) -> list[str]:
    """Line amounts that carry money: zero-price and unpriced lines (add-ons such as "Top Oreo 0") do not."""
    return sorted(str(i.get("price")) for i in items if i.get("price") not in (None, "0"))


def _key_money_lines(items: list[dict]) -> list[list[str]]:
    """The key's line amounts, with priced add-ons either inside their line or as lines of their own."""
    addons = [{"price": x.get("price")} for i in items for x in i.get("sub_items") or []]
    return [_money_lines(items), _money_lines(items + addons)]


def score_receipt(g: dict, p: dict | None) -> dict:
    p = p or {"fields": {}, "items": []}
    out = Counter()
    for f in TOTALS:
        gv, pv = g["fields"].get(f), p["fields"].get(f)
        if f in ZERO_IS_ABSENT:
            gv, pv = (None if gv == "0" else gv), (None if pv == "0" else pv)
        out[("correct" if gv == pv else "wrong" if gv is not None and pv is not None else
             "missed" if gv is not None else "spurious") if (gv is not None or pv is not None) else "both_blank"] += 1
    # A predicted unpriced or zero line named like one of the key's add-ons is that add-on listed separately,
    # not an extra purchase.
    addons = [x.get("name") for i in g["items"] for x in i.get("sub_items") or []]
    read_items = [i for i in p["items"] if not (i.get("price") in (None, "0") and
                                                any(name_similarity(i.get("name"), a) >= NAME_MATCH for a in addons))]
    out["addons_listed_as_lines"] = len(p["items"]) - len(read_items)
    pairs = match_items(g["items"], read_items)
    out["items_gold"] = len(g["items"])
    out["items_read"] = len(read_items)
    out["items_correct"] = sum(item_correct(a, b) for a, b in pairs)
    amounts_ok = out["wrong"] == out["missed"] == out["spurious"] == 0 and \
        _money_lines(read_items) in _key_money_lines(g["items"])
    out["amounts_exact"] = int(amounts_ok)
    out["fully_exact"] = int(amounts_ok and out["items_correct"] == len(g["items"]) == len(read_items))
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
    return a["fields"].get("total") == b["fields"].get("total") and _money_lines(a["items"]) == _money_lines(b["items"])


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
