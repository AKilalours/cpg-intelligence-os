"""Addition 11: the error budget on the real world, and the lanes' live status.

    python scripts/stp.py simulate   # replay the world's cash lines day by day with the gate (no writes to the ledger)
    python scripts/stp.py status     # each lane: mode, reviews, errors, upper bound, open or closed
    python scripts/stp.py frontier   # many rolls per scenario at audit rates 1/5/10/20%, main world; picks the rate
    python scripts/stp.py fresh      # the picked rate, once, on 8 fresh worlds (seeds 201-208) nobody has seen

The simulation reads the engine's replayed suggestions and the answer key; the answer key plays the
person who reviews an audited line (a perfect reviewer, an assumption the report states). Scenarios:
healthy history at three budgets; a silent fault (from 60% of the way through, a share of one lane's
suggestions turn wrong); and a bad release (same fault, new engine version, so its evidence starts
over). The policy numbers (1% budget, 95% confidence, 5% audit, 2-day review delay) were set before
the first run and are not tuned on its result.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cpgos import config  # noqa: E402
from cpgos.db import connect, migrate  # noqa: E402
from cpgos.finance import cashapp, stp  # noqa: E402

BUDGETS = (0.005, 0.01, 0.02)
FAULT_RATES = (0.05, 0.20)


def _items(conn) -> list[stp.Item]:
    truth = dict(conn.execute("SELECT remittance_line_id, invoice_id FROM eval.gt_remittance_alloc"))
    return stp.items_from(cashapp.match_all(conn, "full", replay=True), truth, cashapp.ENGINE_VERSION)


def _brief(r: dict) -> dict:
    return {k: v for k, v in r.items() if k != "timeline"}


def scenarios(items: list[stp.Item]) -> dict:
    days = sorted({i.day for i in items})
    start = days[int(len(days) * 0.6)]
    lane = max(stp.LANES.values(), key=lambda l: sum(1 for i in items if i.lane == l))
    base = stp.Budget()
    out = {"lines": len(items), "days": [str(days[0]), str(days[-1])], "budget": base.__dict__,
           "reviewer": "the answer key (a perfect person); a real reviewer also errs",
           "reviews_needed_to_open": {str(b): stp.reviews_needed(b) for b in BUDGETS},
           "healthy": {}, "silent_fault": {}, "bad_release": {}}
    for b in BUDGETS:
        out["healthy"][str(b)] = _brief(stp.simulate(items, stp.Budget(max_error_rate=b)))
    out["healthy_no_gate"] = stp.no_gate(items)
    for rate in FAULT_RATES:
        f = stp.inject_fault(items, lane, start, rate)
        g = stp.simulate(f, base)
        out["silent_fault"][str(rate)] = {"lane": lane, "from": str(start), "gate": _brief(g),
                                          "no_gate": stp.no_gate(f),
                                          "timeline_gate": g["timeline"],
                                          "timeline_no_gate": _cumulative_wrong(f)}
    f = stp.inject_fault(items, lane, start, 0.10, version="next")
    out["bad_release"] = {"lane": lane, "from": str(start), "fault_rate": 0.10,
                          "gate": _brief(stp.simulate(f, base)), "no_gate": stp.no_gate(f)}
    return out


def _cumulative_wrong(items: list[stp.Item]) -> list[dict]:
    out, w = [], 0
    for d in sorted({i.day for i in items}):
        w += sum(1 for i in items if i.day == d and not i.correct)
        out.append({"day": str(d), "wrong_posted": w})
    return out


def cmd_simulate(conn) -> int:
    from sidecar_world import sibling
    worlds = {"main (generator 1.1)": conn}
    side_url = sibling(config.get("CPGOS_DATABASE_URL"))[0]
    side = None
    try:
        side = psycopg.connect(side_url, autocommit=True)
        if side.execute("SELECT to_regclass('eval.gt_remittance_alloc')").fetchone()[0]:
            migrate(side)
            worlds["side world (generator 1.2, short-pays)"] = side
    except psycopg.OperationalError:
        pass
    body = {"version": stp.SIM_VERSION, "engine": cashapp.ENGINE_VERSION, "worlds": {}}
    for name, c in worlds.items():
        items = _items(c)
        body["worlds"][name] = scenarios(items)
    if side:
        side.close()
    rid = conn.execute("INSERT INTO eval.report (kind, code_version, body) VALUES ('stp', %s, %s) RETURNING report_id",
                       (stp.SIM_VERSION, Jsonb(json.loads(json.dumps(body, default=str))))).fetchone()[0]
    for name, w in body["worlds"].items():
        print(f"\n== {name}: {w['lines']} lines the engine would post, {w['days'][0]} to {w['days'][1]}")
        print(f"   clean reviews needed to open a lane: {w['reviews_needed_to_open']}")
        for b, r in w["healthy"].items():
            print(f"   healthy, budget {float(b):.1%}: posted {r['posted_by_engine']}, to a person {r['sent_to_a_person']}, "
                  f"wrong posted {r['wrong_posted']}, dollars posted {r['dollars_posted_share']}, opened {r['opened']}")
        print(f"   healthy, no gate: posted {w['healthy_no_gate']['posted_by_engine']}, wrong {w['healthy_no_gate']['wrong_posted']}")
        for rate, r in w["silent_fault"].items():
            print(f"   silent fault {float(rate):.0%} in {r['lane']} from {r['from']}: wrong posted gate {r['gate']['wrong_posted']} "
                  f"vs no gate {r['no_gate']['wrong_posted']}; caught in review {r['gate']['wrong_caught_in_review']}; "
                  f"closed {r['gate']['closed_after_opening']}")
        br = w["bad_release"]
        print(f"   bad release (10% wrong) from {br['from']}: wrong posted gate {br['gate']['wrong_posted']} "
              f"vs no gate {br['no_gate']['wrong_posted']}; opened {br['gate']['opened']}")
    print(f"\nreport {rid} saved (kind 'stp')")
    return 0


def cmd_status(conn) -> int:
    print(json.dumps(stp.overview(conn, cashapp.ENGINE_VERSION), indent=1, default=str))
    return 0


RUNS = 200
FRESH_SEEDS = tuple(range(201, 209))


def _show(name: str, r: dict) -> str:
    w, n = r["wrong_posted_with_gate"], r["wrong_posted_no_gate"]
    extra = ""
    if "lines_to_close" in r:
        lc = r["lines_to_close"]
        extra = (f"; every faulty lane closed in {r['runs_where_every_faulty_lane_closed']}/{r['runs']} rolls"
                 + (f", lines to close median {lc['median']:.0f} p90 {lc['p90']:.0f}" if lc else ""))
    return (f"   {name:<18} wrong posted with gate median {w['median']:.0f} p90 {w['p90']:.0f} | no gate median "
            f"{n['median']:.0f} | to a person {r['sent_to_a_person']['median']:.0f} | dollars posted "
            f"{r['dollars_posted_share']['median']:.1%}{extra}")


def cmd_frontier(conn) -> int:
    items = _items(conn)
    fr = stp.frontier([items], RUNS)
    chosen = stp.choose_audit_rate(fr)
    body = {"version": stp.SIM_VERSION, "engine": cashapp.ENGINE_VERSION, "world": "main (generator 1.1)",
            "lines": len(items), "runs": RUNS, "rule": stp.choose_audit_rate.__doc__.split("(decision 94): ")[1].strip(),
            "frontier": fr, "chosen_audit_rate": chosen}
    rid = conn.execute("INSERT INTO eval.report (kind, code_version, body) VALUES ('stp_frontier', %s, %s) RETURNING report_id",
                       (stp.SIM_VERSION, Jsonb(json.loads(json.dumps(body, default=str))))).fetchone()[0]
    print(f"main world, {len(items)} lines the engine would post, {RUNS} rolls per scenario, budget 1% at 95%")
    for rate, sc in fr.items():
        print(f"\n audit {float(rate):.0%}")
        for name, r in sc.items():
            print(_show(name, r))
    print(f"\nchosen audit rate by the rule written beforehand: {chosen:.0%}   (report {rid}, kind 'stp_frontier')")
    return 0


def cmd_fresh(conn) -> int:
    import os
    import time
    from urllib.parse import urlsplit
    from agent_seeds import _parquet, _url
    from cpgos.finance.world import WorldConfig, build_world, weekly_sales_from_parquet
    from cpgos.ingest.reference import load_deduction_codes
    done = conn.execute("SELECT report_id FROM eval.report WHERE kind = 'stp_fresh' AND code_version = %s",
                        (stp.SIM_VERSION,)).fetchone()
    if done:
        print(f"already scored for {stp.SIM_VERSION} (report {done[0]}); fresh worlds are scored once per version")
        return 0
    fr = conn.execute("SELECT report_id, body FROM eval.report WHERE kind = 'stp_frontier' AND code_version = %s "
                      "ORDER BY report_id DESC LIMIT 1", (stp.SIM_VERSION,)).fetchone()
    if not fr:
        print("run make stp-frontier first: the audit rate is chosen there, on the main world, before this runs")
        return 1
    rate = float(fr[1]["chosen_audit_rate"])
    url = config.get("CPGOS_DATABASE_URL")
    main_db = urlsplit(url).path.lstrip("/")
    base, admin = f"{main_db}_m5base", _url(url, "postgres")
    with psycopg.connect(admin, autocommit=True) as a:
        if not a.execute("SELECT 1 FROM pg_database WHERE datname = %s", (base,)).fetchone():
            print(f"no base database {base}: run make agent-seeds once (it loads M5 into it), then this")
            return 1
    pq = _parquet()
    worlds = []
    for ws in FRESH_SEEDS:
        db, t0 = f"{main_db}_stp{ws}", time.time()
        with psycopg.connect(admin, autocommit=True) as a:
            a.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
            a.execute(f'CREATE DATABASE "{db}" TEMPLATE "{base}"')
        try:
            with psycopg.connect(_url(url, db), autocommit=True) as c:
                migrate(c)
                cfg = WorldConfig(seed=ws)
                load_deduction_codes(c)
                build_world(c, weekly_sales_from_parquet(pq, cfg.weeks, cfg.n_skus, cfg.seed), cfg,
                            source_note=f"stp fresh seed {ws}")
                worlds.append(_items(c))
            print(f"world {ws}: {len(worlds[-1])} lines the engine would post  {time.time() - t0:.0f}s", flush=True)
        finally:
            if os.environ.get("KEEP") != "1":
                with psycopg.connect(admin, autocommit=True) as a:
                    a.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
    b = stp.Budget(audit_rate=rate)
    res = {"healthy": stp.monte_carlo(worlds, b, RUNS),
           "fault_5pct": stp.monte_carlo(worlds, b, RUNS, fault_rate=0.05),
           "fault_20pct": stp.monte_carlo(worlds, b, RUNS, fault_rate=0.20),
           "bad_release_10pct": stp.monte_carlo(worlds, b, RUNS, fault_rate=0.10, version="next")}
    body = {"version": stp.SIM_VERSION, "engine": cashapp.ENGINE_VERSION, "seeds": list(FRESH_SEEDS),
            "audit_rate": rate, "from_frontier_report": fr[0], "lines": sum(len(w) for w in worlds),
            "runs": RUNS, "results": res}
    rid = conn.execute("INSERT INTO eval.report (kind, code_version, body) VALUES ('stp_fresh', %s, %s) RETURNING report_id",
                       (stp.SIM_VERSION, Jsonb(json.loads(json.dumps(body, default=str))))).fetchone()[0]
    print(f"\n{len(worlds)} fresh worlds, {body['lines']} lines, audit {rate:.0%} (chosen on the main world), "
          f"{RUNS} rolls, totals across worlds per roll")
    for name, r in res.items():
        print(_show(name, r))
    print(f"\nreport {rid} (kind 'stp_fresh'), scored once for {stp.SIM_VERSION}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["simulate", "status", "frontier", "fresh"])
    args = ap.parse_args()
    with connect() as conn:
        conn.autocommit = True
        migrate(conn)
        return {"simulate": cmd_simulate, "status": cmd_status, "frontier": cmd_frontier,
                "fresh": cmd_fresh}[args.command](conn)


if __name__ == "__main__":
    sys.exit(main())
