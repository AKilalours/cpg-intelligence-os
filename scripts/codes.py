#!/usr/bin/env python3
"""Addition 12: score readers of deduction reason text. Every model is scored on the held-out phrasings once.

    make codes-baselines      keyword rules and character n-gram Naive Bayes (seconds, $0)
    make codes-prompted       the local model with category definitions only (Ollama, $0, ~15 min)
    make codes-lora           fine-tune a small open model with LoRA on this Mac, then score it ($0)
    make codes-report         every stored score side by side
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cpgos.codes import baselines, data, evaluate, llm  # noqa: E402
from cpgos.db import code_version, connect, migrate  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "local_runs"


def _save(conn, name: str, version: str, body: dict) -> int | None:
    """Store a score once per (reader, version): the held-out set is spent when it is read."""
    from psycopg.types.json import Jsonb
    done = conn.execute("SELECT report_id FROM eval.report WHERE kind = 'codes' AND body->>'reader' = %s "
                        "AND body->>'reader_version' = %s", (name, version)).fetchone()
    if done:
        print(f"{name} {version} was already scored (report {done[0]}); a change needs a new version.")
        return None
    return conn.execute("INSERT INTO eval.report (kind, code_version, body) VALUES ('codes', %s, %s) RETURNING report_id",
                        (code_version(), Jsonb(json.loads(json.dumps(body, default=str))))).fetchone()[0]


def _run(conn, name: str, version: str, predict) -> int:
    ex = data.generate()
    body = {"reader": name, "reader_version": version, "data": data.DATA_VERSION,
            "validation": evaluate.score(predict, [e for e in ex if e.split == "validation"]),
            "heldout": evaluate.score(predict, [e for e in ex if e.split == "heldout"])}
    rid = _save(conn, name, version, body)
    if rid is None:
        return 1
    v, h = body["validation"], body["heldout"]
    print(f"report {rid}: {name}  seen phrasings {v['accuracy']['k']}/{v['n']}  unseen phrasings {h['accuracy']['k']}/{h['n']}"
          f" (macro-F1 {h['macro_f1']})  hardest: {', '.join(x['family'] for x in h['hardest_phrasings'][:3])}")
    return 0


def cmd_baselines(conn) -> int:
    tr = [e for e in data.generate() if e.split == "train"]
    rc = 0
    for m in (baselines.Keywords(tr), baselines.CharNaiveBayes(tr)):
        rc |= _run(conn, m.name, "1.0.0", m.predict)
    return rc


def cmd_prompted(conn) -> int:
    from cpgos.llm import LLMError, provider
    p = provider("ollama")
    try:
        p.digest()
    except LLMError as e:
        print(f"Ollama is not reachable ({e}). Start it with: ollama serve")
        return 1
    return _run(conn, f"prompted {p.model}", llm.PROMPT_VERSION, llm.prompted(p))


def cmd_score_lora(conn) -> int:
    f = OUT / "codes_lora_predictions.json"
    if not f.exists():
        print("No LoRA predictions yet: make codes-lora")
        return 1
    run = json.loads(f.read_text())
    if run["data"] != data.DATA_VERSION:
        print(f"predictions are for {run['data']}, data is {data.DATA_VERSION}: retrain")
        return 1
    preds = run["predictions"]
    return _run(conn, f"LoRA {run['base_model']}", run["adapter_version"], lambda t: preds.get(t))


def cmd_report(conn) -> int:
    rows = conn.execute("SELECT report_id, body FROM eval.report WHERE kind = 'codes' ORDER BY report_id").fetchall()
    print(f"{'reader':52} {'seen':>9} {'unseen':>9} {'unseen 95% CI':>16} {'macro-F1':>9}")
    for rid, b in rows:
        v, h = b["validation"]["accuracy"], b["heldout"]["accuracy"]
        ci = h["ci95"] or [0, 0]
        print(f"{(b['reader'] + ' ' + b['reader_version'])[:52]:52} {v['rate']:>9.1%} {h['rate']:>9.1%}"
              f" {ci[0]:>7.1%}-{ci[1]:<7.1%} {b['heldout']['macro_f1']:>9.3f}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["baselines", "prompted", "score-lora", "report"])
    a = ap.parse_args()
    with connect() as conn:
        conn.autocommit = True
        migrate(conn)
        return {"baselines": cmd_baselines, "prompted": cmd_prompted, "score-lora": cmd_score_lora,
                "report": cmd_report}[a.command](conn)


if __name__ == "__main__":
    sys.exit(main())
