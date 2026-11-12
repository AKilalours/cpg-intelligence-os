"""Phase 4 drift monitors: false alarms on healthy history, and how fast realistic changes are seen.

    python scripts/drift.py design   # main world: false alarms, detection of 5 injected changes (rerunnable)
    python scripts/drift.py fresh    # once per version: 8 fresh worlds (seeds 301-308), unseen reason wordings

The policy (alpha 1e-4 per feature per weekly check, PSI 0.10, a 60-event window, the first 40% of
history as the baseline) was written before the first run (decision 97). The design run may show it is
wrong; any change is made on the main world only and then scored once on fresh worlds.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cpgos import config  # noqa: E402
from cpgos.db import connect, migrate  # noqa: E402
from cpgos.monitor import drift, scenarios as sc  # noqa: E402

ROLLS = int(os.environ.get("ROLLS") or 40)
FRESH_SEEDS = tuple(range(301, 309))


def _save(conn, kind: str, body: dict) -> int:
    return conn.execute("INSERT INTO eval.report (kind, code_version, body) VALUES (%s, %s, %s) RETURNING report_id",
                        (kind, drift.DRIFT_VERSION, Jsonb(json.loads(json.dumps(body, default=str))))).fetchone()[0]


def _print(fa: dict, det: dict) -> None:
    for name, f in fa.items():
        first = f"; first {f['first']['day']} on {f['first']['feature']}: {f['first']['moved'][:2]}" if f["first"] else ""
        print(f"   healthy {name:<11} weeks checked {f['weeks_checked']:>4}, weeks with a false alarm {f['weeks_with_an_alarm']}{first}")
    for name, d in det.items():
        print(f"   {name:<26} caught in {d['detected']}/{d['rolls']} within 120 days; days median {d['days_to_alarm']['median']} "
              f"p90 {d['days_to_alarm']['p90']}; {d['stream']} events median {d['events_to_alarm']['median']}; "
              f"alarmed before the change in {d['rolls_with_an_alarm_before_the_change']}")


def run(worlds: list[sc.World], families: dict, policy: drift.Policy, rolls: int) -> tuple[dict, dict]:
    fa: dict = {}
    for i, w in enumerate(worlds):
        for name, f in sc.false_alarms(sc.streams(w), policy).items():
            agg = fa.setdefault(name, {"weeks_checked": 0, "weeks_with_an_alarm": 0, "first": None})
            agg["weeks_checked"] += f["weeks_checked"]
            agg["weeks_with_an_alarm"] += f["weeks_with_an_alarm"]
            agg["first"] = agg["first"] or f["first"]
    det: dict = {}
    for name in sc.SCENARIOS:
        parts = [sc.detection(w, name, policy, rolls, families, seed0=1000 * i) for i, w in enumerate(worlds)]
        days = [x for p in parts for x in ([p["days_to_alarm"]["median"]] if p["days_to_alarm"]["median"] is not None else [])]
        det[name] = {"stream": parts[0]["stream"], "rolls": sum(p["rolls"] for p in parts),
                     "detected": sum(p["detected"] for p in parts),
                     "days_to_alarm": {"median": sorted(days)[len(days) // 2] if days else None,
                                       "p90": max((p["days_to_alarm"]["p90"] or 0) for p in parts) if days else None},
                     "events_to_alarm": {"median": parts[0]["events_to_alarm"]["median"]},
                     "rolls_with_an_alarm_before_the_change": sum(p["rolls_with_an_alarm_before_the_change"] for p in parts),
                     "per_world": parts}
    return fa, det


def cmd_design(conn) -> int:
    t = time.time()
    policy = drift.Policy()
    fa, det = run([sc.load(conn)], sc.SET1, policy, ROLLS)
    rid = _save(conn, "drift_design", {"policy": policy.__dict__, "rolls": ROLLS, "false_alarms": fa, "detection": det,
                                       "reason_wordings": "development set (unseen set 1)"})
    print(f"main world, {drift.DRIFT_VERSION}, policy {policy.__dict__}, {ROLLS} rolls per change")
    _print(fa, det)
    print(f"report {rid} (kind 'drift_design')  {time.time() - t:.0f}s")
    return 0


def cmd_fresh(conn) -> int:
    from urllib.parse import urlsplit
    from agent_seeds import _parquet, _url
    from cpgos.finance.world import WorldConfig, build_world, weekly_sales_from_parquet
    from cpgos.ingest.reference import load_deduction_codes
    done = conn.execute("SELECT report_id FROM eval.report WHERE kind = 'drift_fresh' AND code_version = %s",
                        (drift.DRIFT_VERSION,)).fetchone()
    if done:
        print(f"already scored for {drift.DRIFT_VERSION} (report {done[0]}); fresh worlds are scored once per version")
        return 0
    url = config.get("CPGOS_DATABASE_URL")
    main_db = urlsplit(url).path.lstrip("/")
    base, admin = f"{main_db}_m5base", _url(url, "postgres")
    with psycopg.connect(admin, autocommit=True) as a:
        if not a.execute("SELECT 1 FROM pg_database WHERE datname = %s", (base,)).fetchone():
            print(f"no base database {base}: run make agent-seeds once (it loads M5 into it), then this")
            return 1
    pq, worlds = _parquet(), []
    for ws in FRESH_SEEDS:
        db, t0 = f"{main_db}_drift{ws}", time.time()
        with psycopg.connect(admin, autocommit=True) as a:
            a.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
            a.execute(f'CREATE DATABASE "{db}" TEMPLATE "{base}"')
        try:
            with psycopg.connect(_url(url, db), autocommit=True) as c:
                migrate(c)
                cfg = WorldConfig(seed=ws)
                load_deduction_codes(c)
                build_world(c, weekly_sales_from_parquet(pq, cfg.weeks, cfg.n_skus, cfg.seed), cfg,
                            source_note=f"drift fresh seed {ws}")
                worlds.append(sc.load(c))
            print(f"world {ws}: {len(worlds[-1].lines)} cash lines, {len(worlds[-1].deductions)} claims, "
                  f"{len(worlds[-1].prices)} price rows  {time.time() - t0:.0f}s", flush=True)
        finally:
            with psycopg.connect(admin, autocommit=True) as a:
                a.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
    policy = drift.Policy()
    fa, det = run(worlds, sc.SET2, policy, max(5, ROLLS // 4))
    rid = _save(conn, "drift_fresh", {"policy": policy.__dict__, "seeds": list(FRESH_SEEDS), "false_alarms": fa,
                                      "detection": det, "reason_wordings": "unseen set 2"})
    print(f"\n{len(worlds)} fresh worlds, {drift.DRIFT_VERSION}, unseen reason wordings (set 2)")
    _print(fa, det)
    print(f"report {rid} (kind 'drift_fresh'), scored once for {drift.DRIFT_VERSION}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["design", "fresh"])
    args = ap.parse_args()
    with connect() as conn:
        conn.autocommit = True
        migrate(conn)
        return {"design": cmd_design, "fresh": cmd_fresh}[args.command](conn)


if __name__ == "__main__":
    sys.exit(main())
