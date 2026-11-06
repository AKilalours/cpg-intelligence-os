#!/usr/bin/env python3
"""Addition 12: score readers of deduction reason text. Every model is scored on the held-out phrasings once.

    make codes-baselines      keyword rules and character n-gram Naive Bayes (seconds, $0)
    make codes-prompted       the local model with category definitions only (Ollama, $0, ~15 min)
    make codes-lora           fine-tune a small open model with LoRA on this Mac, then score it ($0)
    make codes-hybrid         Naive Bayes when confident, else the local model; threshold chosen on the
                              development set (the first unseen set), scored on the second unseen set
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
                        "AND body->>'reader_version' = %s AND body->>'data' = %s",
                        (name, version, body["data"])).fetchone()
    if done:
        print(f"{name} {version} was already scored (report {done[0]}); a change needs a new version.")
        return None
    return conn.execute("INSERT INTO eval.report (kind, code_version, body) VALUES ('codes', %s, %s) RETURNING report_id",
                        (code_version(), Jsonb(json.loads(json.dumps(body, default=str))))).fetchone()[0]


def _run(conn, name: str, version: str, predict, extra: dict | None = None) -> int:
    ex = data.generate()
    body = {"reader": name, "reader_version": version, "data": data.DATA_VERSION, **(extra or {}),
            "validation": evaluate.score(predict, [e for e in ex if e.split == "validation"]),
            "heldout": evaluate.score(predict, [e for e in ex if e.split == "heldout"]),
            "heldout2": evaluate.score(predict, [e for e in ex if e.split == "heldout2"])}
    rid = _save(conn, name, version, body)
    if rid is None:
        return 1
    v, h, h2 = body["validation"], body["heldout"], body["heldout2"]
    print(f"report {rid}: {name}  seen {v['accuracy']['k']}/{v['n']}  unseen set 1 {h['accuracy']['k']}/{h['n']}"
          f"  unseen set 2 {h2['accuracy']['k']}/{h2['n']} (macro-F1 {h2['macro_f1']})"
          f"  hardest: {', '.join(x['family'] for x in h2['hardest_phrasings'][:3])}")
    return 0


def cmd_baselines(conn) -> int:
    tr = [e for e in data.generate() if e.split == "train"]
    rc = 0
    for m in (baselines.Keywords(tr), baselines.CharNaiveBayes(tr)):
        rc |= _run(conn, m.name, "1.0.0", m.predict)
    return rc


def cmd_prompted(conn) -> int:
    p = _local()
    return 1 if p is None else _run(conn, f"prompted {p.model}", llm.PROMPT_VERSION, llm.prompted(p))


def _local():
    from cpgos.llm import LLMError, provider
    p = provider("ollama")
    try:
        p.digest()
    except LLMError as e:
        print(f"Ollama is not reachable ({e}). Start it with: ollama serve")
        return None
    return p


def cmd_hybrid(conn) -> int:
    """Pick the Naive Bayes confidence threshold on the development set (the first unseen set, already read),
    then score the hybrid once, mainly on the second unseen set, which nothing was designed on."""
    p = _local()
    if p is None:
        return 1
    ex = data.generate()
    nb = baselines.CharNaiveBayes([e for e in ex if e.split == "train"])
    ask = llm.prompted(p)
    dev = [e for e in ex if e.split == "heldout"]
    llm_dev = {e.text: ask(e.text) for e in dev}           # recorded from make codes-prompted: replays for $0
    margins = sorted({round(nb.margin(e.text), 3) for e in ex if e.split in ("heldout", "validation")})
    best = None
    for t in [0.0] + margins:
        right = sum((nb.predict(e.text) if nb.margin(e.text) >= t else llm_dev[e.text]) == e.category for e in dev)
        calls = sum(nb.margin(e.text) < t for e in dev)
        if best is None or right > best[1] or (right == best[1] and calls < best[2]):
            best = (t, right, calls)
    t, right, calls = best
    print(f"threshold {t} chosen on the development set: {right}/{len(dev)} right, model asked on {calls}")
    h = baselines.Hybrid(nb, ask, t)
    return _run(conn, "hybrid NB + " + p.model, f"hybrid-1.0.0@{t}", h.predict,
                {"threshold": t, "chosen_on": "heldout (development)", "dev_right": right, "dev_model_calls": calls})


def cmd_paired(conn) -> int:
    """Not a new score: the frozen readers' answers on unseen set 2 (replayed, $0), compared pairwise on the
    same phrasings with an exact McNemar test, which totals alone cannot give."""
    from cpgos.agent.seeds import mcnemar_exact
    p = _local()
    if p is None:
        return 1
    ex = data.generate()
    nb = baselines.CharNaiveBayes([e for e in ex if e.split == "train"])
    rows = conn.execute("SELECT body->>'threshold' FROM eval.report WHERE kind = 'codes' AND body->>'reader' LIKE 'hybrid%' "
                        "ORDER BY report_id DESC LIMIT 1").fetchone()
    if rows is None:
        print("No hybrid score yet: make codes-hybrid")
        return 1
    ask = llm.prompted(p)
    readers = {"Naive Bayes": nb.predict, f"prompted {p.model}": ask,
               "hybrid": baselines.Hybrid(nb, ask, float(rows[0])).predict}
    test = [e for e in ex if e.split == "heldout2"]
    right = {name: [f(e.text) == e.category for e in test] for name, f in readers.items()}
    names = list(readers)
    out = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a_, b_ = right[names[i]], right[names[j]]
            only_a = sum(x and not y for x, y in zip(a_, b_))
            only_b = sum(y and not x for x, y in zip(a_, b_))
            out.append({"a": names[i], "b": names[j], "right_only_a": only_a, "right_only_b": only_b,
                        "p_value": round(mcnemar_exact(only_a, only_b), 6)})
            print(f"{names[i]:>22} vs {names[j]:<22} right only for the first {only_a:4d}, only for the second {only_b:4d}"
                  f"   exact McNemar p = {out[-1]['p_value']}")
    (OUT / "codes_paired.json").write_text(json.dumps(out, indent=1))
    return 0


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
    print(f"{'reader':50} {'seen':>7} {'unseen 1':>9} {'unseen 2':>9} {'unseen 2, 95% CI':>17}")
    for rid, b in rows:
        v, h = b["validation"]["accuracy"], b["heldout"]["accuracy"]
        h2 = (b.get("heldout2") or {}).get("accuracy")
        two = f"{h2['rate']:>9.1%} {h2['ci95'][0]:>8.1%}-{h2['ci95'][1]:<8.1%}" if h2 else f"{'not run':>9} {'':17}"
        print(f"{(b['reader'] + ' ' + b['reader_version'])[:50]:50} {v['rate']:>7.1%} {h['rate']:>9.1%} {two}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["baselines", "prompted", "hybrid", "paired", "score-lora", "report"])
    a = ap.parse_args()
    with connect() as conn:
        conn.autocommit = True
        migrate(conn)
        return {"baselines": cmd_baselines, "prompted": cmd_prompted, "hybrid": cmd_hybrid, "paired": cmd_paired,
                "score-lora": cmd_score_lora,
                "report": cmd_report}[a.command](conn)


if __name__ == "__main__":
    sys.exit(main())
