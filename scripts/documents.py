"""Phase 2b commands for the Document Intelligence Engine.

Usage:
    python scripts/documents.py load-fatura    # register FATURA invoices and write the answer key to eval.*
    python scripts/documents.py train-router   # learn layout fingerprints (fit layouts); choose grid and threshold on calibration
    python scripts/documents.py eval-router    # score the frozen router once on held-out invoices; store the report
    python scripts/documents.py route          # route every document as the engine role
    python scripts/documents.py learn-rules    # learn field areas and label vocabulary from fit layouts
    python scripts/documents.py extract        # OCR + rules on every document not yet extracted (engine role)
    python scripts/documents.py eval-extract   # score the frozen rules on known-layout test and holdout layouts
    python scripts/documents.py vlm-dev        # choose the local model's image scale on 20 fit-layout dev invoices
    python scripts/documents.py eval-cascade   # tier 1 + local model on fixed holdout and calibration samples (resumable)
    python scripts/documents.py vlm-collect    # run tier 1 + local model on fresh round-2 samples; prints no scores
    python scripts/documents.py routing-estimate             # how many Claude calls each step needs, and the cost; no calls
    python scripts/documents.py routing --split calibration  # two-readers rule + Claude on calibration (design data)
    python scripts/documents.py routing --split holdout      # the one-time final score on fresh holdout invoices

Requires CPGOS_DATABASE_URL and FATURA in data/raw/fatura (make download DATASET=fatura).
No language model is called by these commands.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cpgos.preflight import require_packages  # noqa: E402  (stdlib only)

require_packages()

from cpgos.db import code_version, connect, migrate  # noqa: E402
from cpgos.documents import extraction, layout, ocr, rules  # noqa: E402
from cpgos.documents.fatura import FaturaFormatError, load_fatura  # noqa: E402

RAW = ROOT / "data" / "raw"
LOCK = ROOT / "data" / "datasets.lock.json"


def cmd_load_fatura(conn) -> int:
    zip_sha = None
    if LOCK.exists():
        zip_sha = json.loads(LOCK.read_text()).get("fatura", {}).get("FATURA2.zip")
    t = time.time()
    try:
        rep = load_fatura(conn, RAW, zip_sha256=zip_sha)
    except FaturaFormatError as e:
        print(f"BLOCKED: {e}")
        return 1
    out = asdict(rep)
    out["provenance"] = "FATURA2.zip sha256 from datasets.lock.json" if zip_sha else "manifest hash of files"
    out["seconds"] = round(time.time() - t, 1)
    print(json.dumps(out, indent=1))
    return 0


def cmd_train_router(conn) -> int:
    t = time.time()
    router, info = layout.train(conn, RAW)
    mid = layout.save(conn, router)
    print(json.dumps({"layout_model_id": mid, "version": router.version, "chosen": router.trained_on,
                      "layouts": len(router.keys), "settings_tried_on_calibration": info["tried"],
                      "seconds": round(time.time() - t, 1)}, indent=1, default=str))
    return 0


def cmd_eval_router(conn) -> int:
    from psycopg.types.json import Jsonb
    t = time.time()
    mid, router = layout.load(conn)
    rep = layout.evaluate(conn, RAW, router)
    rep["layout_model_id"] = mid
    rid = conn.execute("INSERT INTO eval.report (kind, code_version, body) VALUES ('layout_routing', %s, %s) RETURNING report_id",
                       (code_version(), Jsonb(json.loads(json.dumps(rep, default=str))))).fetchone()[0]
    rep["report_id"], rep["seconds"] = rid, round(time.time() - t, 1)
    print(json.dumps(rep, indent=1, default=str))
    return 0


def cmd_route(conn) -> int:
    t = time.time()
    out = layout.route_documents(conn, RAW)
    out["seconds"] = round(time.time() - t, 1)
    print(json.dumps(out, indent=1))
    return 0


def cmd_learn_rules(conn) -> int:
    model = rules.learn(conn)
    mid = rules.save_model(conn, model)
    print(json.dumps({"rules_model_id": mid, "version": model.version, "trained_on": model.trained_on,
                      "layouts_with_field_areas": len(model.regions),
                      "labels": {f: [lab for lab, _ in labs] for f, labs in model.lexicon.items()}}, indent=1))
    return 0


def _ocr_ready() -> bool:
    try:
        print(f"OCR engine: {ocr.engine_id()}")
        return True
    except ocr.OcrUnavailable as e:
        print(f"BLOCKED: {e}")
        return False


def cmd_extract(conn) -> int:
    if not _ocr_ready():
        return 1
    t = time.time()
    out = extraction.extract_documents(conn, RAW)
    out["seconds"] = round(time.time() - t, 1)
    print(json.dumps(out, indent=1))
    return 0


def cmd_eval_extract(conn) -> int:
    from psycopg.types.json import Jsonb
    if not _ocr_ready():
        return 1
    t = time.time()
    rep = extraction.evaluate(conn, RAW)
    body = json.loads(json.dumps(rep, default=str))
    rid = conn.execute("INSERT INTO eval.report (kind, code_version, body) VALUES ('extraction_rules', %s, %s) RETURNING report_id",
                       (code_version(), Jsonb(body))).fetchone()[0]
    short = lambda s: {k: s["all_fields"][k] for k in ("correct", "wrong", "abstained", "spurious")} | {
        "precision": s["all_fields"]["precision"]["rate"], "coverage": s["all_fields"]["coverage"]["rate"]}
    print(json.dumps({"report_id": rid, "extractor_version": rep["extractor_version"],
                      "known_layouts": short(rep["known_layouts"]), "new_layouts_holdout": short(rep["new_layouts_holdout"]),
                      "by_field_new_layouts": {f: {k: v[k] for k in ("correct", "wrong", "abstained", "spurious")}
                                               for f, v in rep["new_layouts_holdout"]["by_field"].items()},
                      "seconds": round(time.time() - t, 1)}, indent=1))
    return 0


def cmd_vlm_dev(conn) -> int:
    """Image scale for the local vision model, decided on fit-layout dev invoices only."""
    import random
    from collections import defaultdict
    from psycopg.types.json import Jsonb
    from cpgos.documents import vlm
    from cpgos.llm import LLMError, provider
    p = provider("ollama")
    try:
        print(f"model: {p.model} digest {str(p.digest())[:12]}")
    except LLMError as e:
        print(f"BLOCKED: {e}")
        return 1
    gs = conn.execute("SELECT max(gold_set_id) FROM eval.gold_set WHERE name = 'fatura_header_fields'").fetchone()[0]
    docs = conn.execute("""SELECT s.source_document_id, s.storage_key, s.sha256 FROM eval.doc_meta m
                           JOIN source_document s USING (source_document_id)
                           WHERE m.split_unseen = 'fit' AND m.split_seen = 'dev' ORDER BY m.doc_key""").fetchall()
    docs = random.Random(2026).sample(docs, 20)
    gold = defaultdict(dict)
    for sid, f, v in conn.execute("SELECT source_document_id, field_name, value FROM eval.doc_gold WHERE gold_set_id = %s "
                                  "AND source_document_id = ANY(%s)", (gs, [d[0] for d in docs])):
        gold[sid][f] = v
    report = {}
    for scale in (1.0, 2.0):
        pairs, secs, errors, by_field = [], [], 0, defaultdict(list)
        for i, (sid, key, sha) in enumerate(docs, 1):
            exs, meta = vlm.extract(p, vlm.page_png(RAW / key, scale), vlm.image_id(sha, scale))
            if "error" in meta:
                errors += 1
            secs.append(meta.get("seconds") or 0)
            got = {e.field_name: e.value for e in exs}
            for f in vlm.FIELD_HELP:
                pairs.append((gold[sid].get(f), got.get(f)))
                by_field[f].append((gold[sid].get(f), got.get(f)))
            print(f"  scale {scale}: {i}/20 ({meta.get('seconds')} s{', replayed' if meta.get('replayed') else ''})", flush=True)
        s = extraction._score(pairs)
        report[f"scale_{scale}"] = {"fields": {k: s[k] for k in ("correct", "wrong", "abstained", "spurious")},
                                    "precision": s["precision"], "coverage": s["coverage"], "failed_calls": errors,
                                    "seconds_per_invoice": round(sum(secs) / len(secs), 1),
                                    "seller_name": extraction._score(by_field["seller_name"])["coverage"]}
    rid = conn.execute("INSERT INTO eval.report (kind, code_version, body) VALUES ('vlm_dev', %s, %s) RETURNING report_id",
                       (code_version(), Jsonb(json.loads(json.dumps({"model": p.model, **report}, default=str))))).fetchone()[0]
    print(json.dumps({"report_id": rid, "model": p.model, **report}, indent=1, default=str))
    return 0


def cmd_eval_cascade(conn) -> int:
    from psycopg.types.json import Jsonb
    from cpgos.documents import cascade
    from cpgos.llm import LLMError, provider
    if not _ocr_ready():
        return 1
    p = provider("ollama")
    try:
        print(f"model: {p.model} digest {str(p.digest())[:12]}")
    except LLMError as e:
        print(f"BLOCKED: {e}")
        return 1
    rep = cascade.evaluate(conn, RAW, p, progress=lambda m: print(m, flush=True))
    body = json.loads(json.dumps(rep, default=str))
    rid = conn.execute("INSERT INTO eval.report (kind, code_version, body) VALUES ('extraction_cascade', %s, %s) RETURNING report_id",
                       (code_version(), Jsonb(body))).fetchone()[0]
    def short(s):
        return {k: s[k] for k in ("correct", "wrong", "abstained", "spurious")} | {
            "precision": s["precision"]["rate"], "coverage": s["coverage"]["rate"]}
    print(json.dumps({"report_id": rid, "minutes": rep["minutes"],
                      **{split: {"invoices": rep[split]["invoices"], "tier1": short(rep[split]["tier1"]),
                                 "tier2_alone": short(rep[split]["tier2"]), "cascade": short(rep[split]["cascade"]),
                                 "tier2_filled": rep[split]["tier2_filled"], "readers_agree": rep[split]["readers_agree"]}
                         for split in ("holdout", "calibration")}}, indent=1, default=str))
    return 0


def cmd_vlm_collect(conn) -> int:
    from cpgos.documents import cascade
    from cpgos.llm import LLMError, provider
    if not _ocr_ready():
        return 1
    p = provider("ollama")
    try:
        print(f"model: {p.model} digest {str(p.digest())[:12]}")
    except LLMError as e:
        print(f"BLOCKED: {e}")
        return 1
    t = time.time()
    out = cascade.collect(conn, RAW, p, round_=2, progress=lambda m: print(m, flush=True))
    print(json.dumps({"collected": {k: {x: v.get(x) for x in ("documents", "failed_calls")} for k, v in out.items()},
                      "minutes": round((time.time() - t) / 60, 1), "note": "no scores printed on purpose"}, indent=1))
    return 0


SPLIT_ROUNDS = {"calibration": [1, 2], "holdout": [2]}   # holdout round 1 was seen during design


def _local_model():
    from cpgos.llm import LLMError, provider
    p = provider("ollama")
    try:
        p.digest()
    except LLMError:
        # Ollama not running: recorded answers are enough, because every local-model answer is recorded.
        p = provider("ollama", mode="replay")
    return p


def cmd_routing_estimate(conn) -> int:
    from cpgos.documents import cascade, routing
    from cpgos.llm import budget
    if not _ocr_ready():
        return 1
    local = _local_model()
    out = {}
    for split, rounds in SPLIT_ROUNDS.items():
        ids = sorted({sid for r in rounds for sid in cascade.sample(conn, r)[split]})
        v1 = extraction.extract_documents(conn, RAW, ids)["extractor_version"]
        v2 = cascade.run_tier2(conn, RAW, ids, local, progress=lambda m: None)["extractor_version"]
        r1, r2 = cascade._values(conn, v1, ids), cascade._values(conn, v2, ids)
        need = sum(1 for sid in ids if routing.needs_third_reader(r1[sid], r2[sid]))
        typical = budget.cost("claude-sonnet-5-5", 1400, 300) * need
        worst = budget.cost("claude-sonnet-5-5", 1700, 700) * need
        out[split] = {"invoices": len(ids), "need_claude": need, "usd_typical": f"{typical:.2f}", "usd_worst_case": f"{worst:.2f}"}
    out["budget"] = {"cap_usd": str(budget.cap()), "spent_usd": f"{budget.spent():.4f}",
                     "prices_per_mtok": "claude-sonnet-5-5: $2 in / $10 out (platform.claude.com pricing, 2026-10-30)"}
    print(json.dumps(out, indent=1))
    return 0


def cmd_routing(conn, split: str, use_claude: bool) -> int:
    from psycopg.types.json import Jsonb
    from cpgos.documents import cascade
    from cpgos.llm import LLMError, provider
    from cpgos.llm.budget import BudgetExceeded
    if not _ocr_ready():
        return 1
    claude = provider("anthropic") if use_claude else None
    t = time.time()
    try:
        rep = cascade.evaluate_routing(conn, RAW, split, SPLIT_ROUNDS[split], _local_model(), claude,
                                       progress=lambda m: print(m, flush=True))
    except (BudgetExceeded, LLMError) as e:
        print(f"STOPPED: {e}")
        return 1
    rep["minutes"] = round((time.time() - t) / 60, 1)
    body = json.loads(json.dumps(rep, default=str))
    rid = conn.execute("INSERT INTO eval.report (kind, code_version, body) VALUES (%s, %s, %s) RETURNING report_id",
                       (f"routing_{split}", code_version(), Jsonb(body))).fetchone()[0]
    short = lambda s: s if "incomplete" in s else {
        "straight_through": s["straight_through"], "accepted_correct": s.get("accepted_correct"),
        "review_fields_per_invoice": s["review_fields_per_invoice"], "accepted_by_reason": s.get("accepted_by_reason")}
    print(json.dumps({"report_id": rid, "split": split, "invoices": rep["invoices"], "third_reader": rep["third_reader"],
                      "two_readers_only": short(rep["two_readers_only"]), "with_claude": short(rep["with_third_reader"]),
                      "accepted_but_wrong": rep["accepted_but_wrong"][:12], "minutes": rep["minutes"]}, indent=1, default=str))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["calibration", "holdout"], default="calibration")
    ap.add_argument("--no-claude", action="store_true", help="decide with the first two readers only")
    ap.add_argument("command", choices=["load-fatura", "train-router", "eval-router", "route", "routing-estimate", "routing",
                                        "learn-rules", "extract", "eval-extract", "vlm-dev",
                                        "eval-cascade", "vlm-collect"])
    args = ap.parse_args()
    with connect() as conn:
        conn.autocommit = True
        migrate(conn)
        return {"load-fatura": cmd_load_fatura, "train-router": cmd_train_router,
                "eval-router": cmd_eval_router, "route": cmd_route, "learn-rules": cmd_learn_rules,
                "extract": cmd_extract, "eval-extract": cmd_eval_extract, "vlm-dev": cmd_vlm_dev,
                "eval-cascade": cmd_eval_cascade, "vlm-collect": cmd_vlm_collect,
                "routing-estimate": cmd_routing_estimate,
                "routing": lambda c: cmd_routing(c, args.split, not args.no_claude)}[args.command](conn)


if __name__ == "__main__":
    sys.exit(main())
