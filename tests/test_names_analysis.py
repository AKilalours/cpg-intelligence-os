from cpgos.documents.names_analysis import NameCase, analyze, is_candidate


def c(local, claude, ocr, truth, on_page=True, field="seller_name"):
    return NameCase(1, field, ocr, local, claude, truth, on_page)


def test_only_model_agreement_that_ocr_does_not_confirm_is_a_candidate():
    assert is_candidate(c("Acme Ltd", "Acme Ltd", "Acme Lid", "Acme Ltd"))
    assert is_candidate(c("Acme Ltd", "Acme Ltd", None, "Acme Ltd"))
    assert not is_candidate(c("Acme Ltd", "Acme Ltd", "Acme Ltd", "Acme Ltd"))     # 1.4 already accepts
    assert not is_candidate(c("Acme Ltd", "Acme Inc", None, "Acme Ltd"))            # models disagree


def test_a_rule_is_proposed_only_when_its_lower_bound_clears_the_bar():
    many_right = [c("A", "A", None, "A") for _ in range(300)]
    assert analyze(many_right, 0.98)["agree"]["worth_proposing"] is True
    few = [c("A", "A", None, "A") for _ in range(20)]                                # 20/20 is not enough evidence
    assert analyze(few, 0.98)["agree"]["worth_proposing"] is False


def test_the_page_check_variant_drops_names_grounding_could_not_find():
    cases = [c("A", "A", None, "A")] * 50 + [c("Tidied", "Tidied", "Printed", "Printed", on_page=False)] * 5
    out = analyze(cases, 0.9)
    assert out["agree"]["would_accept"] == 55 and out["agree"]["right"]["k"] == 50
    assert out["agree_on_page"]["would_accept"] == 50 and out["agree_on_page"]["right"]["k"] == 50
    assert out["agree"]["wrong"][0] == {"doc": 1, "field": "seller_name", "models": "Tidied", "ocr": "Printed", "truth": "Printed"}


def test_non_name_fields_are_ignored():
    assert analyze([c("7", "7", None, "7", field="total")], 0.9)["agree"]["would_accept"] == 0


def _script():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("documents_cli", Path(__file__).parents[1] / "scripts" / "documents.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_command_needs_a_round_5_bar_and_reads_it(conn, capsys):
    import json
    from psycopg.types.json import Jsonb
    cli = _script()
    assert cli.cmd_names_analysis(conn) == 1 and "round 5" in capsys.readouterr().out
    conn.execute("INSERT INTO eval.report (kind, code_version, body) VALUES ('routing_holdout', 'x', %s)",
                 (Jsonb({"rounds": [5], "with_third_reader": {"accepted_correct": {"ci95": [0.9972, 0.9996]}}}),))
    assert cli.cmd_names_analysis(conn) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["bar_lower_bound"] == 0.9972 and out["agree"]["would_accept"] == 0 and out["development_invoices"] == 0
