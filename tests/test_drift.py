"""Drift monitors: the statistics, the features, and that a healthy stream stays quiet while a
changed one alarms."""

from __future__ import annotations

import random
from collections import Counter
from datetime import date, timedelta

import pytest

from cpgos.monitor import drift


@pytest.mark.parametrize("x,df,p", [(3.841459, 1, 0.05), (5.991465, 2, 0.05), (6.634897, 1, 0.01),
                                    (18.307038, 10, 0.05), (0.0, 3, 1.0), (40.0, 4, 4.3284e-08)])
def test_chi_square_tail_matches_tables(x, df, p):
    assert drift.chi2_sf(x, df) == pytest.approx(p, rel=2e-3)


def test_psi_is_zero_for_the_same_distribution_and_grows_with_change():
    a = {"x": 0.5, "y": 0.5}
    assert drift.psi(a, a) == 0
    assert drift.psi(a, {"x": 0.6, "y": 0.4}) < drift.psi(a, {"x": 0.9, "y": 0.1})


def test_reference_shape_keeps_format_not_value():
    assert drift.shape("INV-0012345") == drift.shape("inv-99") == "A-9"
    assert drift.shape("PO 4471882") == "A 9" and drift.shape(None) == "none" and drift.shape("  ") == "none"


def _events(n, start, pick, rng):
    return [drift.Event(start + timedelta(days=i // 2), (("f", pick(rng)),)) for i in range(n)]


def test_a_healthy_stream_raises_no_alarm():
    rng = random.Random(1)
    ev = _events(2000, date(2024, 1, 1), lambda r: r.choices("abc", [0.7, 0.2, 0.1])[0], rng)
    alarms, _ = drift.watch("s", ev)
    assert alarms == []


def test_a_new_format_alarms_quickly_and_says_what_moved():
    rng = random.Random(2)
    ev = _events(1200, date(2024, 1, 1), lambda r: r.choices("abc", [0.7, 0.2, 0.1])[0], rng)
    start = date(2024, 1, 1) + timedelta(days=450)
    ev += [drift.Event(start + timedelta(days=i // 2), (("f", "NEW" if rng.random() < 0.5 else "a"),)) for i in range(200)]
    alarms, _ = drift.watch("s", ev)
    assert alarms and alarms[0].day <= start + timedelta(days=21)
    assert alarms[0].check.moved[0]["value"] == "other"                 # the unseen value is named as the change


def test_tiny_but_significant_shifts_do_not_alarm_without_practical_size():
    base = Counter({"a": 50000, "b": 50000})
    c = drift.compare("f", base, ["a"] * 5250 + ["b"] * 4750, alpha=1e-4, min_psi=0.1)
    assert c.p_value < 1e-4 and c.psi < 0.1 and not c.alarm


def test_price_events_flag_drops_against_the_prior_high():
    weeks = [date(2024, 1, 1) + timedelta(weeks=i) for i in range(10)]
    rows = [(w, 1, 1, 2.0) for w in weeks[:9]] + [(weeks[9], 1, 1, 1.5)]
    ev = drift.price_events(rows)
    assert [drift.feats(e)["shelf_price_drop"] for e in ev] == ["no"] * 5 + ["yes"]


@pytest.fixture
def dworld(conn):
    from cpgos.finance.world import build_world
    from cpgos.ingest.reference import load_deduction_codes
    from tests.test_finance_world import cfg, sales_fixture
    load_deduction_codes(conn)
    build_world(conn, sales_fixture(), cfg())
    return conn


def test_scenarios_change_inputs_and_the_real_engine_reacts(dworld):
    from cpgos.monitor import scenarios as sc
    w = sc.load(dworld)
    assert w.lines and w.deductions
    st = sc.streams(w)
    assert len(st["cash"]) == len(w.lines)
    start = min(l[1] for l in w.lines)
    po = sc.streams(sc.po_numbers(w, start, 1.0, random.Random(0)))["cash"]
    assert {drift.feats(e)["reference_shape"] for e in po} == {"A9"}
    outcomes = Counter(drift.feats(e)["engine_outcome"] for e in po)
    assert outcomes["exact_reference"] == 0                       # no PO number names an invoice
    sp = sc.streams(sc.short_pay_wave(w, start, 1.0, random.Random(0)))["cash"]
    assert Counter(drift.feats(e)["netted_short_pay"] for e in sp)["yes"] > len(sp) // 2
    words = sc.new_reason_words(w, start, 1.0, random.Random(0), sc.SET1)
    assert words.deductions != w.deductions and len(words.deductions) == len(w.deductions)


def test_reader_is_sure_about_the_worlds_own_reason_words():
    from cpgos.monitor import scenarios as sc
    r = sc.reader()
    for text, cat in sc.WORLD_REASONS.items():
        assert r.margin(text) >= sc.HYBRID_THRESHOLD and r.predict(text) == cat


def test_design_command_runs_and_saves(dworld, monkeypatch, capsys):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import drift as script
    monkeypatch.setattr(script, "ROLLS", 2)
    assert script.cmd_design(dworld) == 0
    body = dworld.execute("SELECT body FROM eval.report WHERE kind = 'drift_design'").fetchone()[0]
    assert set(body["detection"]) == set(__import__("cpgos.monitor.scenarios", fromlist=["x"]).SCENARIOS)
    assert set(body["false_alarms"]) == {"cash", "deductions", "prices"}


def _trend(n=3000, start=date(2014, 1, 1)):
    """A healthy stream whose 'netted' share grows slowly from 3% to 15% over the history: what world
    generator 1.2 does as its deduction program ramps up (decision 99)."""
    rng = random.Random(9)
    return [drift.Event(start + timedelta(days=i // 4),
                        (("netted", "yes" if rng.random() < 0.03 + 0.12 * i / n else "no"),)) for i in range(n)]


def test_regression_a_slow_healthy_trend_alarmed_against_a_fixed_baseline():
    # drift-1.0.0 compared every window with the first 40% of history and alarmed in one healthy week in
    # four on unseen worlds (report 42). The moving baseline must stay quiet on the same kind of stream.
    ev = _trend()
    fixed, _ = drift.watch("cash", ev, drift.Policy(baseline="fixed"))
    moving, _ = drift.watch("cash", ev, drift.Policy())
    assert fixed and moving == []


def test_a_sudden_change_still_alarms_against_a_moving_baseline():
    ev = _trend()
    cut = ev[int(len(ev) * 0.75)].day
    ev = [e if e.day < cut else drift.Event(e.day, (("netted", "yes" if random.Random(e.day.toordinal() * 7 + i).random() < 0.5 else "no"),))
          for i, e in enumerate(ev)]
    alarms, _ = drift.watch("cash", ev, drift.Policy())
    assert alarms and cut <= alarms[0].day <= cut + timedelta(days=21)


def test_fresh_refuses_when_the_design_world_used_another_generator(dworld, capsys):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import drift as script
    script._save(dworld, "drift_design", {"generator": "1.1.0"})
    assert script.cmd_fresh(dworld) == 1
    assert "refusing" in capsys.readouterr().out
    assert dworld.execute("SELECT count(*) FROM eval.report WHERE kind = 'drift_fresh'").fetchone()[0] == 0


def test_the_world_records_its_generator(dworld):
    from cpgos.finance.world import GENERATOR_VERSION
    from cpgos.monitor import scenarios as sc
    assert sc.load(dworld).generator == GENERATOR_VERSION
