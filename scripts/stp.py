"""Addition 11: the error budget on the real world, and the lanes' live status.

    python scripts/stp.py simulate   # replay the world's cash lines day by day with the gate (no writes to the ledger)
    python scripts/stp.py status     # each lane: mode, reviews, errors, upper bound, open or closed

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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["simulate", "status"])
    args = ap.parse_args()
    with connect() as conn:
        conn.autocommit = True
        migrate(conn)
        return {"simulate": cmd_simulate, "status": cmd_status}[args.command](conn)


if __name__ == "__main__":
    sys.exit(main())
