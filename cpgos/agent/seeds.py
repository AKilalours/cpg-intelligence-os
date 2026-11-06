"""The agent against the engine on many fresh worlds, compared claim by claim.

One world gave the agent 0 false disputes against the engine's 2, on 38 disputes: too few to tell a real
difference from luck (the 95% interval for 0 of 38 reaches 9%). Here the frozen agent and engine are run
on several new worlds, each with its own world seed and evidence-damage seed, so every claim is new to
both. Every claim is recorded with what the engine did, what the agent did, and the truth; results are
pooled, and the two are compared on the same claims with an exact McNemar test: of the claims where
exactly one of them was wrong, how often was it the engine?
"""

from __future__ import annotations

from math import comb

import psycopg

from cpgos.agent import investigate as inv
from cpgos.finance import cashapp, deductions, evidence
from cpgos.finance.evaluate import rate
from cpgos.finance.world import WorldConfig, build_world
from cpgos.ingest.reference import load_deduction_codes

SEEDS_VERSION = "agent-seeds-1.0.0"


def engine_action(verdict: str) -> str:
    return {"valid": "accept", "invalid": "dispute", "partially_valid": "dispute"}.get(verdict, "undecided")


def run_world(conn: psycopg.Connection, sales, world_seed: int, evidence_seed: int, region_of=None) -> list[dict]:
    """Build a world, damage its evidence, run the engine and the agent; one row per claim."""
    load_deduction_codes(conn)
    build_world(conn, sales, WorldConfig(seed=world_seed), source_note=f"seed {world_seed}")
    evidence.degrade(conn, evidence.EvidenceConfig(seed=evidence_seed))
    cashapp.apply(conn, cashapp.match_all(conn, "full"))
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_engine")
        assessed = deductions.assess_all(conn)
    deductions.save(conn, assessed)
    findings = inv.investigate(conn)
    truth = {d: (v, r) for d, v, r in conn.execute("SELECT deduction_id, is_valid, reason FROM eval.gt_deduction")}
    region = {sid: code[:2] for sid, code in conn.execute("SELECT store_id, external_code FROM store")}
    store = dict(conn.execute("SELECT deduction_id, store_id FROM deduction"))
    return [{"world_seed": world_seed, "evidence_seed": evidence_seed, "claim": f.claim_number,
             "region": region.get(store.get(f.deduction_id)), "truth_valid": truth[f.deduction_id][0],
             "truth_reason": truth[f.deduction_id][1], "engine": engine_action(f.engine_verdict),
             "engine_rule": f.engine_rule, "agent": f.action}
            for f in findings if f.deduction_id in truth]


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact p-value: b and c are the claims where only the first, or only the second, was wrong."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)


def pool(rows: list[dict], regions: tuple[str, ...] | None = None) -> dict:
    r = [x for x in rows if regions is None or x["region"] in regions]
    def fd(who):  # disputed a valid claim
        return [x for x in r if x[who] == "dispute"], [x for x in r if x[who] == "dispute" and x["truth_valid"]]
    def fa(who):  # accepted an invalid claim
        return [x for x in r if x[who] == "accept"], [x for x in r if x[who] == "accept" and not x["truth_valid"]]
    out = {"claims": len(r), "worlds": len({x["world_seed"] for x in r})}
    for who in ("engine", "agent"):
        dn, dk = fd(who)
        an, ak = fa(who)
        out[who] = {"false_disputes": rate(len(dk), len(dn)), "false_approvals": rate(len(ak), len(an)),
                    "undecided_or_requests": sum(x[who] in ("undecided", "request") for x in r)}
    # Paired, on the same claims: wrong for only one of the two.
    e_fd = {(x["world_seed"], x["claim"]) for x in r if x["engine"] == "dispute" and x["truth_valid"]}
    a_fd = {(x["world_seed"], x["claim"]) for x in r if x["agent"] == "dispute" and x["truth_valid"]}
    e_fa = {(x["world_seed"], x["claim"]) for x in r if x["engine"] == "accept" and not x["truth_valid"]}
    a_fa = {(x["world_seed"], x["claim"]) for x in r if x["agent"] == "accept" and not x["truth_valid"]}
    out["paired"] = {
        "false_disputes": {"engine_only": len(e_fd - a_fd), "agent_only": len(a_fd - e_fd), "both": len(e_fd & a_fd),
                           "p_value": round(mcnemar_exact(len(e_fd - a_fd), len(a_fd - e_fd)), 6)},
        "false_approvals": {"engine_only": len(e_fa - a_fa), "agent_only": len(a_fa - e_fa), "both": len(e_fa & a_fa),
                            "p_value": round(mcnemar_exact(len(e_fa - a_fa), len(a_fa - e_fa)), 6)},
    }
    # What the agent did with the engine's false disputes, and the cost: real over-claims it asked about.
    out["engine_false_disputes_became"] = {a: sum(1 for x in r if x["engine"] == "dispute" and x["truth_valid"] and x["agent"] == a)
                                           for a in ("dispute", "request", "accept")}
    out["invalid_claims_agent_asked_about_instead_of_disputing"] = sum(
        1 for x in r if x["engine"] == "dispute" and not x["truth_valid"] and x["agent"] == "request")
    return out
