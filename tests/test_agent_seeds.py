"""Many fresh worlds, claim by claim: the pooled comparison and the exact paired test."""

from __future__ import annotations

import pytest

from cpgos.agent import seeds
from tests.test_finance_world import sales_fixture


def test_mcnemar_exact_matches_known_values():
    assert seeds.mcnemar_exact(0, 0) == 1.0
    assert seeds.mcnemar_exact(6, 0) == pytest.approx(2 / 64)          # 6 discordant, all one way
    assert seeds.mcnemar_exact(3, 3) == 1.0
    assert seeds.mcnemar_exact(10, 1) == pytest.approx(2 * 12 / 2048)


def test_pooling_counts_each_claim_once_and_pairs_on_the_same_claim():
    row = lambda w, c, valid, eng, ag, reg="TX": {"world_seed": w, "claim": c, "region": reg, "truth_valid": valid,
                                                    "truth_reason": "x", "engine": eng, "engine_rule": "r", "agent": ag}
    rows = [row(1, "A", True, "dispute", "request"), row(2, "A", True, "dispute", "request"),   # same id, two worlds
            row(1, "B", False, "dispute", "dispute"), row(1, "C", True, "accept", "accept"),
            row(1, "D", False, "dispute", "request"), row(1, "E", True, "dispute", "dispute", "CA")]
    p = seeds.pool(rows)
    assert p["claims"] == 6 and p["worlds"] == 2
    assert p["paired"]["false_disputes"] == {"engine_only": 2, "agent_only": 0, "both": 1, "p_value": 0.5}
    assert p["engine_false_disputes_became"] == {"dispute": 1, "request": 2, "accept": 0}
    assert p["invalid_claims_agent_asked_about_instead_of_disputing"] == 1
    assert seeds.pool(rows, ("TX",))["claims"] == 5


def test_a_fresh_world_is_run_end_to_end_and_seeds_change_it(db_url, conn, template_db):
    import uuid
    import psycopg
    from tests.conftest import ADMIN_URL, _url_for
    rows = seeds.run_world(conn, sales_fixture(), world_seed=3, evidence_seed=11)
    assert rows and {"engine", "agent", "truth_valid"} <= set(rows[0])
    other = f"cpgos_t_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as a:
        a.execute(f'CREATE DATABASE "{other}" TEMPLATE "{template_db}"')
    with psycopg.connect(_url_for(other), autocommit=True) as c2:
        rows2 = seeds.run_world(c2, sales_fixture(), world_seed=4, evidence_seed=12)
    sig = lambda rs: sorted((x["claim"], x["truth_reason"], x["engine"]) for x in rs)
    assert sig(rows) != sig(rows2)
