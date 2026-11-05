"""Phase 3 investigator: each playbook on damaged evidence, the role boundary, and the audit trail."""

from __future__ import annotations

from decimal import Decimal

import psycopg
import pytest

from cpgos.agent import investigate as inv
from cpgos.agent.score import score
from cpgos.agent.tools import price_drop_weeks
from cpgos.finance import deductions, evidence
from cpgos.finance.world import build_world
from cpgos.ingest.reference import load_deduction_codes
from tests.test_finance_world import cfg, sales_fixture


def _world(conn, damage: dict | None = None, **kw):
    """A world, its shelf prices in sales_observation (as M5 provides), the engine run, then damage."""
    load_deduction_codes(conn)
    sales = sales_fixture(weeks=kw.get("weeks", 40))
    build_world(conn, sales, cfg(**kw))
    ver = conn.execute("SELECT max(dataset_version_id) FROM dataset_version").fetchone()[0]
    sku = dict(conn.execute("SELECT sku_code, sku_id FROM product").fetchall())
    store = dict(conn.execute("SELECT external_code, store_id FROM store").fetchall())
    with conn.cursor() as cur:
        cur.executemany("""INSERT INTO sales_observation (obs_date, granularity, sku_id, store_id, units, shelf_price,
                                                          dataset_version_id) VALUES (%s, 'week', %s, %s, %s, %s, %s)""",
                        [(s.week, sku[s.item], store[s.store], s.units, s.price, ver) for s in sales])
    if damage is not None:
        evidence.degrade(conn, evidence.EvidenceConfig(**{k: 0.0 for k in evidence.EvidenceConfig.__dataclass_fields__
                                                          if k.startswith("p_")} | damage))
    deductions.save(conn, deductions.assess_all(conn))


def test_price_drop_is_the_usual_tpr_signal():
    from datetime import date, timedelta
    w0 = date(2016, 1, 4)
    prices = [{"week": w0 + timedelta(days=7 * i), "shelf_price": p} for i, p in
              enumerate(["2.00"] * 8 + ["1.70", "2.00"])]
    assert [d["week"] for d in price_drop_weeks(prices, w0, w0 + timedelta(days=70))] == [str(w0 + timedelta(days=56))]
    assert price_drop_weeks(prices[:8] + [{"week": w0 + timedelta(days=56), "shelf_price": "1.85"}], w0,
                            w0 + timedelta(days=70)) == []                       # 7.5% off is not a promotion


def test_a_missing_agreement_with_a_promotional_price_is_asked_about_not_disputed(conn):
    _world(conn, damage={"p_promo_missing": 1.0}, p_promo_overclaim=0.0)
    out = inv.investigate(conn)
    no_agreement = [f for f in out if f.engine_rule == "scan_back_no_agreement"]
    assert no_agreement, "scenario did not occur"
    truth = dict(conn.execute("SELECT deduction_id, is_valid FROM eval.gt_deduction").fetchall())
    for f in no_agreement:
        if truth[f.deduction_id]:      # a real promotion whose record is missing: ask, do not dispute
            assert f.action == "request" and "agreement" in f.request and f.facts[0]["value"]
    s = score(conn, out)["all"]
    assert s["false_disputes"]["k"] < s["engine"]["false_disputes"]["k"]


def test_a_claim_for_a_week_with_no_price_drop_is_still_disputed(conn):
    _world(conn, damage={}, p_promo_unsupported=1.0)
    truth = dict(conn.execute("SELECT deduction_id, reason FROM eval.gt_deduction").fetchall())
    out = inv.investigate(conn)
    calm = [f for f in out if truth[f.deduction_id] == "no_agreement"]
    assert calm and all(f.action == "dispute" for f in calm)


def test_a_claim_without_a_store_is_checked_at_the_only_store_that_ran_the_promotion(conn):
    _world(conn, damage={"p_claim_store_missing": 1.0}, p_promo_overclaim=0.5)
    truth = dict(conn.execute("SELECT deduction_id, is_valid FROM eval.gt_deduction").fetchall())
    out = [f for f in inv.investigate(conn) if f.engine_rule == "scan_back_missing_fields"]
    assert out, "scenario did not occur"
    inferred = [f for f in out if any(x["fact"] == "store inferred" for x in f.facts)]
    assert inferred and all((f.action == "accept") == truth[f.deduction_id] for f in inferred)
    assert all(f.calculation_ids for f in inferred)


def test_every_dispute_cites_its_arithmetic_and_the_agent_changes_no_ledger_value(conn):
    _world(conn, damage={"p_pos_week_missing": 0.2, "p_receipt_missing": 0.2}, p_promo_overclaim=0.5)
    before = conn.execute("SELECT md5(string_agg(deduction_id || status || amount, ',' ORDER BY deduction_id)) "
                          "FROM deduction").fetchone()[0]
    out = inv.investigate(conn)
    assert all(f.calculation_ids for f in out if f.action == "dispute")
    assert all(f.request for f in out if f.action == "request")
    rows = conn.execute("SELECT count(*) FROM calculation WHERE computed_by = 'cpgos.agent.tools'").fetchone()[0]
    assert rows >= sum(f.action == "dispute" for f in out)
    assert inv.save(conn, out) == len(out)
    after = conn.execute("SELECT md5(string_agg(deduction_id || status || amount, ',' ORDER BY deduction_id)) "
                         "FROM deduction").fetchone()[0]
    assert before == after
    a = conn.execute("SELECT actor_type, actor_id, count(*) FROM audit_event WHERE action = 'recommendation' "
                     "GROUP BY 1, 2").fetchall()
    assert a == [("agent", inv.AGENT_VERSION, len(out))]
    assert conn.execute("SELECT count(*) FROM investigation_case").fetchone()[0] == len(out)


def test_the_agent_never_accepts_on_a_price_drop_alone(conn):
    # Prices can only turn a dispute into a request, never into an acceptance.
    _world(conn, damage={"p_promo_missing": 1.0, "p_promo_end_shifted": 1.0}, p_promo_overclaim=0.5)
    out = inv.investigate(conn)
    for f in out:
        if f.action == "accept":
            assert f.engine_verdict == "valid" or any(x["fact"] == "store inferred" for x in f.facts)


def test_scores_are_split_by_region_and_compared_with_the_engine(conn):
    _world(conn, damage={"p_promo_missing": 0.5, "p_claim_store_missing": 0.3}, p_promo_overclaim=0.3)
    s = score(conn, inv.investigate(conn))
    assert s["design_region"]["regions"] == ["CA"] and s["report_region"]["regions"] == ["TX", "WI"]
    for b in (s["all"], s["design_region"]):
        assert b["false_approvals"]["k"] == 0
        assert b["false_disputes"]["k"] <= b["engine"]["false_disputes"]["k"]


def test_the_scoring_region_is_scored_once_per_agent_version(conn, capsys):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("finance_cli", Path(__file__).parents[1] / "scripts" / "finance.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    _world(conn, damage={"p_promo_missing": 0.5})
    assert cli.cmd_agent_design(conn) == 0 and "TX" not in capsys.readouterr().out.split('"agent_differs_from_engine"')[1]
    assert cli.cmd_agent_score(conn) == 0
    assert "false disputes" in capsys.readouterr().out
    assert cli.cmd_agent_score(conn) == 1 and "already scored" in capsys.readouterr().out


def test_with_several_promoting_stores_the_claimed_units_pick_the_store(conn):
    # Found on California (R-0000278): 10 stores promoted the item, so 1.0 asked for the store. A scan-back
    # claims the units one store sold; the store whose POS shows exactly those units is the one.
    _world(conn, damage={"p_claim_store_missing": 1.0}, p_promo_overclaim=0.0)
    other = conn.execute("SELECT store_id FROM store WHERE external_code = 'TX_1'").fetchone()[0]
    for pid, in conn.execute("SELECT promotion_id FROM promotion").fetchall():
        new = conn.execute("""INSERT INTO promotion (brand_id, partner_id, promo_type, start_date, end_date, store_id,
                                  allowance_per_unit, dataset_version_id)
                              SELECT brand_id, partner_id, promo_type, start_date, end_date, %s, allowance_per_unit,
                                     dataset_version_id FROM promotion WHERE promotion_id = %s
                              RETURNING promotion_id""", (other, pid)).fetchone()[0]
        conn.execute("INSERT INTO promotion_sku SELECT %s, sku_id FROM promotion_sku WHERE promotion_id = %s", (new, pid))
    truth = dict(conn.execute("SELECT deduction_id, store_id FROM deduction").fetchall())
    out = [f for f in inv.investigate(conn) if f.engine_rule == "scan_back_missing_fields"]
    assert out, "scenario did not occur"
    for f in out:
        fact = {x["fact"]: x["value"] for x in f.facts}
        assert fact["stores that ran the promotion"] == 2
        assert "store inferred" in fact and str(truth[f.deduction_id]) in fact["store inferred"]
        assert f.action == "accept"
