"""The extraction cascade: tier 1 (OCR + rules), then a vision model for what tier 1 left blank.

Analogy: a junior clerk (tier 1) fills in what they can read for certain and
leaves blanks; a second reader (the model) fills the blanks, but every value
it writes must be pointed to on the page, in the right box. The second reader
never overrules the first: on these invoices tier 1 is right 98-99% of the
time when it answers, and the local model, even grounded, is not that good.

Policy 1.0 (chosen on dev invoices, frozen before scoring):
  final value = tier-1 value if tier 1 answered, else the model's grounded value.
Agreement between the two readers is recorded as a signal for step 6
(calibrated confidence), not used to decide anything yet.
"""

from __future__ import annotations

import random
import time
from collections import Counter, defaultdict
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from cpgos.documents import extraction, ocr, rules, vlm
from cpgos.finance.evaluate import rate

POLICY_VERSION = "cascade-1.0.0"
GROUNDING_VERSION = "ground-1.0.0"
SAMPLE_SEED = 2026
SAMPLE_PER_LAYOUT = {"holdout": 15, "calibration": 10}


def tier2_version(provider, rules_model_id: int) -> str:
    digest = (provider.digest() or "unknown")[-12:]
    return f"{vlm.PROMPT_VERSION}+{provider.name}:{provider.model}@{digest}+{GROUNDING_VERSION}+rules{rules_model_id}"


def run_tier2(conn: psycopg.Connection, raw_root: Path, doc_ids: list[int], provider, progress=print) -> dict:
    """Model reads each document; values are grounded on the page and stored as the engine role.
    Resumable: documents already stored under this version are skipped, and model answers are
    recorded, so an interrupted run loses nothing."""
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_engine")
        mid, model = rules.load_model(conn)
        ver = tier2_version(provider, mid)
        docs = conn.execute(
            """SELECT s.source_document_id, s.storage_key, s.sha256 FROM source_document s
               WHERE s.source_document_id = ANY(%s) AND NOT EXISTS (
                   SELECT 1 FROM field_extraction e WHERE e.source_document_id = s.source_document_id
                   AND e.extractor_version = %s) ORDER BY s.source_document_id""", (doc_ids, ver)).fetchall()
    stats = Counter({"documents": 0, "failed_calls": 0, "replayed": 0})
    secs = []
    for i, (sid, key, sha) in enumerate(docs, 1):
        words = ocr.ocr_image(raw_root / key, sha)
        exs, meta = vlm.extract(provider, vlm.page_png(raw_root / key, 1.0), vlm.image_id(sha, 1.0))
        if "error" in meta:
            stats["failed_calls"] += 1
            progress(f"  {i}/{len(docs)} FAILED: {meta['error'][:120]}")
            continue
        exs = vlm.grounded(exs, words, model.lexicon)
        stats["replayed"] += bool(meta.get("replayed"))
        secs.append(meta.get("seconds") or 0.0)
        with conn.transaction():
            conn.execute("SET LOCAL ROLE cpgos_engine")
            for e in exs:
                conn.execute(
                    """INSERT INTO field_extraction (source_document_id, extractor, extractor_version, field_name, value,
                           raw_text, bbox, path, signals) VALUES (%s, 'vlm', %s, %s, %s, %s, %s, %s, %s)""",
                    (sid, ver, e.field_name, e.value, e.raw_text or None, Jsonb(e.bbox) if e.bbox else None, e.path,
                     Jsonb({**e.signals, "call": {k: meta.get(k) for k in ("seconds", "input_tokens", "output_tokens",
                                                                          "recording", "replayed")}})))
        stats["documents"] += 1
        progress(f"  {i}/{len(docs)} {meta.get('seconds')} s{' (replayed)' if meta.get('replayed') else ''}")
    stats["seconds_per_invoice_model"] = round(sum(secs) / len(secs), 1) if secs else None
    return {"extractor_version": ver, "to_run": len(docs), **dict(stats)}


def sample(conn: psycopg.Connection) -> dict[str, list[int]]:
    """A fixed random sample per layout from each unseen split (the model is too slow for every invoice)."""
    out = {}
    for split, k in SAMPLE_PER_LAYOUT.items():
        rows = conn.execute("SELECT layout_id, source_document_id FROM eval.doc_meta WHERE split_unseen = %s "
                            "ORDER BY layout_id, doc_key", (split,)).fetchall()
        by = defaultdict(list)
        for lid, sid in rows:
            by[lid].append(sid)
        rng = random.Random(SAMPLE_SEED)
        out[split] = sorted(sid for lid in sorted(by) for sid in rng.sample(by[lid], min(k, len(by[lid]))))
    return out


def _values(conn, ver: str, ids: list[int]) -> dict[int, dict[str, str | None]]:
    out: dict[int, dict] = defaultdict(dict)
    for sid, f, v in conn.execute("SELECT source_document_id, field_name, value FROM field_extraction "
                                  "WHERE extractor_version = %s AND source_document_id = ANY(%s)", (ver, ids)):
        out[sid][f] = v
    return out


def combine(t1: dict[str, str | None], t2: dict[str, str | None]) -> dict[str, tuple[str | None, str]]:
    """Policy 1.0: tier 1 when it answered, else the grounded model value. Returns field -> (value, source)."""
    out = {}
    for f in set(t1) | set(t2):
        if t1.get(f) is not None:
            out[f] = (t1[f], "tier1")
        elif t2.get(f) is not None:
            out[f] = (t2[f], "tier2")
        else:
            out[f] = (None, "none")
    return out


def evaluate(conn: psycopg.Connection, raw_root: Path, provider, progress=print) -> dict:
    """Run what is missing, then score tier 1, tier 2, and the cascade on the samples."""
    gs = conn.execute("SELECT max(gold_set_id) FROM eval.gold_set WHERE name = 'fatura_header_fields'").fetchone()[0]
    samples = sample(conn)
    runs = {}
    t = time.time()
    for split, ids in samples.items():
        progress(f"{split}: tier 1 (OCR + rules) on {len(ids)} invoices")
        runs[f"{split}_tier1"] = extraction.extract_documents(conn, raw_root, ids)
        progress(f"{split}: tier 2 ({provider.name} {provider.model}) on {len(ids)} invoices")
        runs[f"{split}_tier2"] = run_tier2(conn, raw_root, ids, provider, progress)
    v1 = runs["holdout_tier1"]["extractor_version"]
    v2 = runs["holdout_tier2"]["extractor_version"]
    report = {"policy": POLICY_VERSION, "tier1_version": v1, "tier2_version": v2, "gold_set_id": gs,
              "sample": {k: {"invoices": len(v), "per_layout": SAMPLE_PER_LAYOUT[k], "seed": SAMPLE_SEED}
                         for k, v in samples.items()},
              "runs": runs, "minutes": round((time.time() - t) / 60, 1)}
    for split, ids in samples.items():
        gold = defaultdict(dict)
        for sid, f, v in conn.execute("SELECT source_document_id, field_name, value FROM eval.doc_gold "
                                      "WHERE gold_set_id = %s AND source_document_id = ANY(%s)", (gs, ids)):
            gold[sid][f] = v
        t1, t2 = _values(conn, v1, ids), _values(conn, v2, ids)
        pairs = {"tier1": [], "tier2": [], "cascade": []}
        by_field = defaultdict(lambda: {"tier1": [], "tier2": [], "cascade": []})
        source = Counter()
        agree = Counter()
        for sid in ids:
            merged = combine(t1[sid], t2[sid])
            for f in set(gold[sid]) | set(merged) | set(t1[sid]) | set(t2[sid]):
                g = gold[sid].get(f)
                for name, v in (("tier1", t1[sid].get(f)), ("tier2", t2[sid].get(f)), ("cascade", merged.get(f, (None, ""))[0])):
                    pairs[name].append((g, v))
                    by_field[f][name].append((g, v))
                if merged.get(f, (None, "none"))[1] == "tier2":
                    source["filled_by_tier2"] += 1
                    source["filled_by_tier2_correct"] += merged[f][0] == g
                a, b = t1[sid].get(f), t2[sid].get(f)
                if a is not None and b is not None:
                    agree["both_answered"] += 1
                    if a == b:
                        agree["agreed"] += 1
                        agree["agreed_and_correct"] += a == g
                    else:
                        agree["disagreed"] += 1
                        agree["disagreed_tier1_correct"] += a == g
        report[split] = {
            "invoices": len(ids),
            **{name: extraction._score(p) for name, p in pairs.items()},
            "tier2_filled": {"fields": source["filled_by_tier2"],
                             "correct": rate(source["filled_by_tier2_correct"], source["filled_by_tier2"])},
            "readers_agree": {"when_both_answer": rate(agree["agreed"], agree["both_answered"]),
                              "precision_when_agree": rate(agree["agreed_and_correct"], agree["agreed"]),
                              "tier1_right_when_disagree": rate(agree["disagreed_tier1_correct"], agree["disagreed"])},
            "by_field": {f: {n: {k: s[k] for k in ("correct", "wrong", "abstained", "spurious")}
                             for n, s in ((n, extraction._score(p)) for n, p in d.items())}
                         for f, d in sorted(by_field.items())},
        }
    return report
