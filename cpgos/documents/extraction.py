"""Run tier-1 extraction on documents and score it against the answer key.

Extraction runs as the engine role: it reads the image, the router's
decision, and the learned rules, never eval.*. Scoring runs afterwards with
the answer key and stores its report in eval.report.

Definitions used in every score:
- correct: the typed value equals the answer key's.
- wrong: a value was given and it differs.
- abstained: the field is on the invoice, but no value was given. Abstaining
  is the safe failure: the next tier picks it up.
- spurious: a value was given for a field the invoice does not have.
- precision = correct / (correct + wrong + spurious): when the tier answers,
  how often it is right. This is what decides whether answers can skip review.
- coverage = correct / fields on the invoice.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from cpgos.documents import ocr, rules
from cpgos.finance.evaluate import rate

EXTRACTOR = "rules"


def extractor_version(rules_model_id: int) -> str:
    return f"{rules.RULES_VERSION}+model{rules_model_id}+{ocr.engine_id()}"


def extract_documents(conn: psycopg.Connection, raw_root: Path, doc_ids: list[int] | None = None,
                      workers: int | None = None) -> dict:
    """OCR + rules for documents not yet extracted by this exact version, written as the engine role."""
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_engine")
        mid, model = rules.load_model(conn)
        ver = extractor_version(mid)
        docs = conn.execute(
            """SELECT s.source_document_id, s.storage_key, s.sha256,
                      (SELECT r.layout_key FROM document_route r WHERE r.source_document_id = s.source_document_id
                       ORDER BY r.layout_model_id DESC LIMIT 1)
               FROM source_document s
               WHERE s.mime_type LIKE 'image/%%'
                 AND (%(ids)s::bigint[] IS NULL OR s.source_document_id = ANY(%(ids)s))
                 AND NOT EXISTS (SELECT 1 FROM field_extraction e WHERE e.source_document_id = s.source_document_id
                                 AND e.extractor_version = %(v)s)
               ORDER BY s.source_document_id""", {"ids": doc_ids, "v": ver}).fetchall()
    words = ocr.ocr_many([(raw_root / d[1], d[2]) for d in docs], workers=workers)
    counts = Counter({k: 0 for k in ("documents", "known_layout_documents", "new_layout_documents", "values", "abstained")})
    rows = []
    for (sid, _, _, layout_key), ws in zip(docs, words):
        exs = rules.extract(model, layout_key, ws)
        counts["documents"] += 1
        counts["known_layout_documents" if layout_key else "new_layout_documents"] += 1
        for e in exs:
            counts["values" if e.value is not None else "abstained"] += 1
            rows.append((sid, EXTRACTOR, ver, e.field_name, e.value, e.raw_text or None,
                         Jsonb(e.bbox) if e.bbox else None, e.path, None, Jsonb(e.signals)))
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_engine")
        with conn.cursor().copy(
                """COPY field_extraction (source_document_id, extractor, extractor_version, field_name, value, raw_text,
                                          bbox, path, confidence, signals) FROM STDIN""") as cp:
            for r in rows:
                cp.write_row(r)
    return {"extractor_version": ver, **dict(counts)}


def _score(pairs) -> dict:
    """pairs: iterable of (gold value or None, predicted value or None)."""
    c = Counter()
    for g, p in pairs:
        if g is None and p is None:
            continue
        c["correct" if p == g else "spurious" if g is None else "abstained" if p is None else "wrong"] += 1
    answered = c["correct"] + c["wrong"] + c["spurious"]
    on_invoice = c["correct"] + c["wrong"] + c["abstained"]
    return {**{k: c[k] for k in ("correct", "wrong", "abstained", "spurious")},
            "precision": rate(c["correct"], answered), "coverage": rate(c["correct"], on_invoice)}


def score(conn: psycopg.Connection, doc_ids: list[int], extractor_ver: str, gold_set_id: int) -> dict:
    gold = defaultdict(dict)
    for sid, f, v in conn.execute("SELECT source_document_id, field_name, value FROM eval.doc_gold "
                                  "WHERE gold_set_id = %s AND source_document_id = ANY(%s)", (gold_set_id, doc_ids)):
        gold[sid][f] = v
    pred = defaultdict(dict)
    paths = Counter()
    for sid, f, v, path in conn.execute("SELECT source_document_id, field_name, value, path FROM field_extraction "
                                        "WHERE extractor_version = %s AND source_document_id = ANY(%s)", (extractor_ver, doc_ids)):
        pred[sid][f] = v
        if v is not None:
            paths[path] += 1
    by_field = defaultdict(list)
    for sid in doc_ids:
        for f in set(gold[sid]) | set(pred[sid]):
            by_field[f].append((gold[sid].get(f), pred[sid].get(f)))
    return {"documents": len(doc_ids), "values_by_path": dict(paths),
            "all_fields": _score(p for ps in by_field.values() for p in ps),
            "by_field": {f: _score(ps) for f, ps in sorted(by_field.items())}}


def evaluate(conn: psycopg.Connection, raw_root: Path, workers: int | None = None) -> dict:
    """Score the frozen rules: known layouts on fit-layout 'test' invoices (routed), and new layouts
    on the holdout. Holdout layouts were never used to learn or tune any rule."""
    gs = conn.execute("SELECT max(gold_set_id) FROM eval.gold_set WHERE name = 'fatura_header_fields'").fetchone()[0]
    sets = {
        "known_layouts": [r[0] for r in conn.execute(
            "SELECT source_document_id FROM eval.doc_meta WHERE split_unseen = 'fit' AND split_seen = 'test' ORDER BY 1")],
        "new_layouts_holdout": [r[0] for r in conn.execute(
            "SELECT source_document_id FROM eval.doc_meta WHERE split_unseen = 'holdout' ORDER BY 1")],
    }
    run = {}
    for name, ids in sets.items():
        run[name] = extract_documents(conn, raw_root, ids, workers=workers)
    ver = run["known_layouts"]["extractor_version"]
    mid = int(ver.split("+model")[1].split("+")[0])
    trained = conn.execute("SELECT trained_on FROM rules_model WHERE rules_model_id = %s", (mid,)).fetchone()[0]
    return {"extractor_version": ver, "ocr_engine": ocr.engine_id(), "gold_set_id": gs, "rules_trained_on": trained,
            **{name: score(conn, ids, ver, gs) for name, ids in sets.items()},
            "notes": ["Seller names have no printed label, so new layouts leave them to the model tiers.",
                      "FATURA labels 'BALANCE DUE' as the total on some layouts and as the amount due on others.",
                      "PO number and amount due do not occur on holdout layouts."]}
