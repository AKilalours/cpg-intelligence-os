"""Addition 12 data and baselines: reproducible, balanced, held-out phrasings truly unseen, no prompt leak."""

from cpgos.codes import baselines, data, evaluate, llm


def test_generation_is_reproducible_and_balanced():
    a, b = data.generate(seed=3), data.generate(seed=3)
    assert a == b and a != data.generate(seed=4)
    for split in ("train", "validation", "heldout"):
        n = [e for e in a if e.split == split]
        counts = {c: sum(e.category == c for e in n) for c in data.CATEGORIES}
        assert max(counts.values()) - min(counts.values()) <= 1


def test_heldout_phrasings_never_appear_in_training():
    ex = data.generate()
    train_fams = {e.family for e in ex if e.split != "heldout"}
    held_fams = {e.family for e in ex if e.split == "heldout"}
    assert held_fams and not (train_fams & held_fams)


def test_the_prompt_does_not_leak_heldout_wording():
    assert llm.heldout_phrases_in_prompt() == []


def test_parse_reads_the_first_category_named():
    assert llm.parse(" Late_fee.") == "late_fee" and llm.parse("scan back") == "scan_back"
    assert llm.parse("I think it is spoils, not return") == "spoils" and llm.parse("no idea") is None


def test_baselines_learn_seen_phrasings_and_struggle_on_unseen_ones():
    ex = data.generate()
    tr = [e for e in ex if e.split == "train"]
    val = [e for e in ex if e.split == "validation"]
    held = [e for e in ex if e.split == "heldout"]
    nb = baselines.CharNaiveBayes(tr)
    v, h = evaluate.score(nb.predict, val), evaluate.score(nb.predict, held)
    assert v["accuracy"]["rate"] > 0.95 and h["accuracy"]["rate"] < 0.75     # the gap the models must close
    kw = baselines.Keywords(tr)
    assert evaluate.score(kw.predict, held)["accuracy"]["rate"] < h["accuracy"]["rate"]


def _cli():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("codes_cli", Path(__file__).parents[1] / "scripts" / "codes.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_heldout_is_scored_once_per_reader_version(conn, capsys):
    cli = _cli()
    assert cli.cmd_baselines(conn) == 0
    out = capsys.readouterr().out
    assert "keyword rules" in out and "unseen set 2" in out
    assert cli.cmd_baselines(conn) == 1 and "already scored" in capsys.readouterr().out
    assert cli.cmd_report(conn) == 0 and "Naive Bayes" in capsys.readouterr().out


def test_lora_predictions_are_scored_from_the_file_and_must_match_the_data(conn, capsys, tmp_path, monkeypatch):
    import json
    cli = _cli()
    monkeypatch.setattr(cli, "OUT", tmp_path)
    assert cli.cmd_score_lora(conn) == 1 and "No LoRA predictions" in capsys.readouterr().out
    ex = data.generate()
    preds = {e.text: e.category for e in ex if e.split != "train"}
    f = tmp_path / "codes_lora_predictions.json"
    f.write_text(json.dumps({"base_model": "fake", "adapter_version": "t@1", "data": "reasons-0.0.0", "predictions": preds}))
    assert cli.cmd_score_lora(conn) == 1 and "retrain" in capsys.readouterr().out
    f.write_text(json.dumps({"base_model": "fake", "adapter_version": "t@1", "data": data.DATA_VERSION, "predictions": preds}))
    assert cli.cmd_score_lora(conn) == 0 and "unseen set 2 800/800" in capsys.readouterr().out


def test_the_fine_tuning_prompt_is_short_and_leaks_nothing():
    p = llm.short_prompt("OTIF FINE WK34")
    assert len(p) < 200 and all(c in p for c in data.CATEGORIES)
    assert "charge" not in p and "definitions" not in p.lower()


def test_the_second_unseen_set_is_new_and_leaves_the_first_three_splits_unchanged():
    a = data.generate(heldout2=0)
    b = data.generate()
    assert a == [e for e in b if e.split != "heldout2"]                     # 1.0's splits exactly
    seen = {e.family for e in b if e.split != "heldout2"}
    two = {e.family for e in b if e.split == "heldout2"}
    assert len(two) == 30 and not (seen & two)


def test_the_hybrid_uses_naive_bayes_when_sure_and_the_model_otherwise():
    ex = data.generate()
    nb = baselines.CharNaiveBayes([e for e in ex if e.split == "train"])
    asked = []
    h = baselines.Hybrid(nb, lambda t: asked.append(t) or "coupon", threshold=0.5)      # seen phrasings score >= 0.6
    seen = next(e for e in ex if e.split == "validation")
    assert h.predict(seen.text) == seen.category and not asked             # confident: no model call
    assert h.predict("CATALINA") == "coupon" and asked == ["CATALINA"]      # unsure: the model decides


def test_paired_comparison_reports_who_was_right_alone(conn, capsys, tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    cli = _cli()
    monkeypatch.setattr(cli, "OUT", tmp_path)

    class Always:                                  # a "model" that always answers coupon
        name, model = "fake", "always-coupon"

        def complete(self, req):
            return SimpleNamespace(text="coupon")
    monkeypatch.setattr(cli, "_local", lambda: Always())
    assert cli.cmd_paired(conn) == 1 and "No hybrid score" in capsys.readouterr().out
    from psycopg.types.json import Jsonb
    conn.execute("INSERT INTO eval.report (kind, code_version, body) VALUES ('codes', 'x', %s)",
                 (Jsonb({"reader": "hybrid NB + fake", "threshold": 0.484}),))
    assert cli.cmd_paired(conn) == 0
    out = json.loads((tmp_path / "codes_paired.json").read_text())
    assert len(out) == 3 and all(0 <= x["p_value"] <= 1 for x in out)
    nb_vs_model = next(x for x in out if x["a"] == "Naive Bayes")
    assert nb_vs_model["right_only_a"] > nb_vs_model["right_only_b"]          # Naive Bayes beats "always coupon"
