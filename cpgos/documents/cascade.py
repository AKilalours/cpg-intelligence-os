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
GROUNDING_VERSION = "ground-2.0.0"
SAMPLE_SEED = 2026
MAX_SAME_ERRORS = 3              # identical rejected requests in a row before a run stops
SAMPLE_PER_LAYOUT = {"holdout": 15, "calibration": 10}
ROUND_SIZES = {1: SAMPLE_PER_LAYOUT, 2: SAMPLE_PER_LAYOUT, 3: {"holdout": 30, "calibration": 0}}


def tier2_version(provider, rules_model_id: int) -> str:
    digest = (provider.digest() or "unknown")[-12:]
    return f"{vlm.PROMPT_VERSION}+{provider.name}:{provider.model}@{digest}+{GROUNDING_VERSION}+rules{rules_model_id}"


def run_tier2(conn: psycopg.Connection, raw_root: Path, doc_ids: list[int], provider, progress=print,
              extractor: str = "vlm") -> dict:
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
    same_error_streak, last_error = 0, None
    for i, (sid, key, sha) in enumerate(docs, 1):
        words = ocr.ocr_image(raw_root / key, sha)
        exs, meta = vlm.extract(provider, vlm.page_png(raw_root / key, 1.0), vlm.image_id(sha, 1.0))
        if "error" in meta:
            stats["failed_calls"] += 1
            progress(f"  {i}/{len(docs)} FAILED: {meta['error'][:160]}")
            # Fail fast: a request the provider rejects as invalid will be rejected for every invoice.
            same_error_streak = same_error_streak + 1 if meta["error"] == last_error and meta.get("client_error") else 1
            last_error = meta["error"]
            if same_error_streak >= MAX_SAME_ERRORS:
                stats["stopped_early"] = 1
                progress(f"  stopped: {MAX_SAME_ERRORS} calls in a row were rejected the same way; fix the request and rerun")
                break
            continue
        same_error_streak, last_error = 0, None
        exs = vlm.grounded(exs, words, model.lexicon)
        stats["replayed"] += bool(meta.get("replayed"))
        secs.append(meta.get("seconds") or 0.0)
        with conn.transaction():
            conn.execute("SET LOCAL ROLE cpgos_engine")
            for e in exs:
                conn.execute(
                    """INSERT INTO field_extraction (source_document_id, extractor, extractor_version, field_name, value,
                           raw_text, bbox, path, signals) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (sid, extractor, ver, e.field_name, e.value, e.raw_text or None, Jsonb(e.bbox) if e.bbox else None, e.path,
                     Jsonb({**e.signals, "call": {k: meta.get(k) for k in ("seconds", "input_tokens", "output_tokens",
                                                                          "recording", "replayed")}})))
        stats["documents"] += 1
        progress(f"  {i}/{len(docs)} {meta.get('seconds')} s{' (replayed)' if meta.get('replayed') else ''}")
    stats["seconds_per_invoice_model"] = round(sum(secs) / len(secs), 1) if secs else None
    return {"extractor_version": ver, "to_run": len(docs), **dict(stats)}


def sample(conn: psycopg.Connection, round_: int = 1) -> dict[str, list[int]]:
    """Fixed random samples per layout from each unseen split; rounds never overlap.

    Each round exists because the previous one was seen while redesigning:
    round 1 scored cascade 1.0; round 2 scored routing 1.1 (designed after
    seeing round 1); round 3, twice as large, scores routing 1.2 (designed
    after seeing round 2). Rounds 1 and 2 are drawn exactly as before."""
    out = {}
    for split in ("holdout", "calibration"):
        k = ROUND_SIZES.get(round_, SAMPLE_PER_LAYOUT).get(split, 0)
        rows = conn.execute("SELECT layout_id, source_document_id FROM eval.doc_meta WHERE split_unseen = %s "
                            "ORDER BY layout_id, doc_key", (split,)).fetchall()
        by = defaultdict(list)
        for lid, sid in rows:
            by[lid].append(sid)
        taken = {lid: set() for lid in by}
        picked = []
        for r in range(1, round_ + 1):
            kr = ROUND_SIZES.get(r, SAMPLE_PER_LAYOUT).get(split, 0)
            rng = random.Random(SAMPLE_SEED) if r == 1 else random.Random(SAMPLE_SEED + r)
            picked = []
            for lid in sorted(by):
                pool = by[lid] if r == 1 else [sid for sid in by[lid] if sid not in taken[lid]]
                got = rng.sample(pool, min(kr, len(pool)))
                taken[lid] |= set(got)
                picked += got
        out[split] = sorted(picked) if k else []
    return out


def _printed_typed(conn, ver: str, ids: list[int]) -> tuple[dict[int, dict[str, str]], dict[int, set[str]]]:
    """What a model reader printed, typed, before grounding; and the fields whose value grounding
    could not find anywhere on the page."""
    from cpgos.documents import vlm
    out: dict[int, dict] = defaultdict(dict)
    missing: dict[int, set] = defaultdict(set)
    for sid, f, raw, sig in conn.execute("SELECT source_document_id, field_name, raw_text, signals FROM field_extraction "
                                         "WHERE extractor_version = %s AND source_document_id = ANY(%s)", (ver, ids)):
        v = vlm._typed(f, raw) if raw not in (None, "", "None", "null") else None
        if v is not None:
            out[sid][f] = v
            if "not on page" in ((sig or {}).get("grounding") or ""):
                missing[sid].add(f)
    return out, missing


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


def collect(conn: psycopg.Connection, raw_root: Path, provider, round_: int, progress=print) -> dict:
    """Run tier 1 and the model on a sample round and store the results, without scoring anything.
    Lets slow model runs happen while the policy is designed, without anyone seeing holdout scores."""
    out = {}
    for split, ids in sample(conn, round_).items():
        progress(f"round {round_} {split}: tier 1 on {len(ids)} invoices")
        if not ids:
            continue
        out[f"{split}_tier1"] = extraction.extract_documents(conn, raw_root, ids)
        progress(f"round {round_} {split}: model on {len(ids)} invoices")
        out[f"{split}_tier2"] = run_tier2(conn, raw_root, ids, provider, progress)
    return out


# ---------- step 5 + 6: Claude as the third reader, and the two-readers rule ----------

def _gold(conn, gs: int, ids: list[int]) -> dict[int, dict]:
    g: dict[int, dict] = defaultdict(dict)
    for sid, f, v in conn.execute("SELECT source_document_id, field_name, value FROM eval.doc_gold "
                                  "WHERE gold_set_id = %s AND source_document_id = ANY(%s)", (gs, ids)):
        g[sid][f] = v
    return g


def page_labels(conn: psycopg.Connection, raw_root: Path, ids: list[int]) -> dict[int, set[str]]:
    """Per invoice, the fields whose label is printed on the page (from cached OCR)."""
    from cpgos.documents import routing
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_engine")
        _, model = rules.load_model(conn)
        docs = conn.execute("SELECT source_document_id, storage_key, sha256 FROM source_document "
                            "WHERE source_document_id = ANY(%s)", (ids,)).fetchall()
    vocab = routing.absence_vocabulary(model)
    return {sid: routing.labels_on_page(ocr.ocr_image(raw_root / key, sha), vocab) for sid, key, sha in docs}


def route(conn: psycopg.Connection, raw_root: Path, ids: list[int], local, claude=None, progress=print) -> dict:
    """Readers 1 and 2 for every invoice; Claude only for invoices with an unsettled field; then decide.
    claude=None decides without a third reader (to see how much Claude adds)."""
    from cpgos.documents import routing
    from cpgos.llm import budget
    v1 = extraction.extract_documents(conn, raw_root, ids)["extractor_version"]
    v2 = run_tier2(conn, raw_root, ids, local, progress=lambda m: None)["extractor_version"]   # replays recordings
    r1, r2 = _values(conn, v1, ids), _values(conn, v2, ids)
    printed = page_labels(conn, raw_root, ids)
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_engine")
        unverifiable = routing.unverifiable_absence(routing.absence_vocabulary(rules.load_model(conn)[1]))
    p2, nf2 = _printed_typed(conn, v2, ids)
    seen: dict[int, set] = defaultdict(set)
    need = [sid for sid in ids if routing.needs_third_reader(r1[sid], r2[sid], printed[sid], set(), unverifiable)]
    r3: dict = defaultdict(dict)
    third = {"invoices_needing_third_reader": len(need)}
    if claude is not None and need:
        spent_before = budget.spent()
        progress(f"third reader ({claude.model}) on {len(need)} of {len(ids)} invoices")
        run = run_tier2(conn, raw_root, need, claude, progress, extractor="claude")
        r3 = _values(conn, run["extractor_version"], need)
        p3, nf3 = _printed_typed(conn, run["extractor_version"], need)
        for sid in need:
            seen[sid] = routing.seen_by_two(p2[sid], p3[sid], not_found=(nf2[sid], nf3[sid]))
        third.update({"version": run["extractor_version"], "calls_failed": run["failed_calls"],
                      "complete": run["failed_calls"] == 0 and not run.get("stopped_early"),
                      "usd_spent_this_run": str(budget.spent() - spent_before), "usd_spent_total": str(budget.spent()),
                      "usd_cap": str(budget.cap())})
    decisions = {sid: routing.decide(r1[sid], r2[sid], r3.get(sid) if claude is not None and sid in need else None,
                                     printed[sid], seen[sid], unverifiable)
                 for sid in ids}
    return {"reader_versions": {"rules": v1, "local_model": v2}, "third_reader": third, "decisions": decisions,
            "printed_labels": printed, "seen": seen, "unverifiable": unverifiable}


def evaluate_routing(conn: psycopg.Connection, raw_root: Path, split: str, rounds: list[int], local, claude=None,
                     progress=print) -> dict:
    from cpgos.documents import routing
    gs = conn.execute("SELECT max(gold_set_id) FROM eval.gold_set WHERE name = 'fatura_header_fields'").fetchone()[0]
    ids = sorted({sid for r in rounds for sid in sample(conn, r)[split]})
    res = route(conn, raw_root, ids, local, claude, progress)
    gold = _gold(conn, gs, ids)
    with_third = routing.summarize(res["decisions"], gold)
    without = routing.summarize({sid: routing.decide(*[_values(conn, v, [sid])[sid] for v in res["reader_versions"].values()],
                                                     None, res["printed_labels"][sid], set(), res["unverifiable"])
                                 for sid in ids}, gold)
    wrong = [(sid, d.field_name, d.reason, d.value, gold[sid].get(d.field_name), d.candidates)
             for sid, ds in res["decisions"].items() for d in ds
             if d.status == "accepted" and d.value != gold[sid].get(d.field_name)]
    complete = claude is None or res["third_reader"].get("complete", True)
    if not complete:
        # Never present a run where the third reader failed as a "with Claude" result.
        with_third = {"incomplete": f"{res['third_reader'].get('calls_failed')} third-reader calls failed; "
                                    "fix the error and rerun (finished invoices are kept)"}
        wrong = []
    return {"policy": routing.POLICY_VERSION, "split": split, "rounds": rounds, "invoices": len(ids),
            "reader_versions": res["reader_versions"], "third_reader": res["third_reader"],
            "two_readers_only": without, "with_third_reader": with_third,
            "accepted_but_wrong": [{"doc": a, "field": b, "reason": c, "accepted": d, "truth": e, "candidates": f}
                                   for a, b, c, d, e, f in wrong[:40]]}
