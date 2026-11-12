"""Phase 4 drift monitors: false alarms on healthy history, and how fast realistic changes are seen.

    python scripts/drift.py design   # main world: false alarms, detection of 5 injected changes (rerunnable)
    WORLD=side python scripts/drift.py design   # the same on the side world (generator 1.2, like fresh worlds)
    python scripts/drift.py watch    # run the monitors on the live data; raise alarms (a cash alarm holds the gate)
    python scripts/drift.py dev      # 8 development worlds (cached): 4 candidate settings, one picked by a fixed rule
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cpgos import config  # noqa: E402
from cpgos.db import connect, migrate  # noqa: E402
from cpgos.monitor import drift, scenarios as sc  # noqa: E402

ROLLS = int(os.environ.get("ROLLS") or 40)
# Seeds 301-308 were spent on drift-1.0.0 (report 42). Each version gets worlds nobody has looked at.
FRESH_SEEDS = {"drift-1.0.0": tuple(range(301, 309)), "drift-1.1.0": tuple(range(401, 409)),
               "drift-1.2.0": tuple(range(601, 609))}[drift.DRIFT_VERSION]
DEV_SEEDS = tuple(range(501, 509))       # development worlds: looked at as often as needed, never scored


def _save(conn, kind: str, body: dict) -> int:
    return conn.execute("INSERT INTO eval.report (kind, code_version, body) VALUES (%s, %s, %s) RETURNING report_id",
                        (kind, drift.DRIFT_VERSION, Jsonb(json.loads(json.dumps(body, default=str))))).fetchone()[0]


def _print(fa: dict, det: dict) -> None:
    for name, f in fa.items():
        first = f"; first {f['first']['day']} on {f['first']['feature']}: {f['first']['moved'][:2]}" if f["first"] else ""
        print(f"   healthy {name:<11} weeks checked {f['weeks_checked']:>4}, weeks with a false alarm {f['weeks_with_an_alarm']}, "
              f"false alarms {f.get('episodes', '?')} in {f.get('years_checked', '?')} world-years{first}")
    for name, d in det.items():
        print(f"   {name:<26} caught in {d['detected']}/{d['rolls']} within 120 days; days median {d['days_to_alarm']['median']} "
              f"p90 {d['days_to_alarm']['p90']}; {d['stream']} events median {d['events_to_alarm']['median']}; "
              f"alarmed before the change in {d['rolls_with_an_alarm_before_the_change']}")


def run(worlds: list[sc.World], families: dict, policy: drift.Policy, rolls: int) -> tuple[dict, dict]:
    """rolls per world per change."""
    fa: dict = {}
    for i, w in enumerate(worlds):
        for name, f in sc.false_alarms(sc.streams(w), policy).items():
            agg = fa.setdefault(name, {"weeks_checked": 0, "weeks_with_an_alarm": 0, "episodes": 0,
                                       "years_checked": 0.0, "first": None})
            agg["weeks_checked"] += f["weeks_checked"]
            agg["weeks_with_an_alarm"] += f["weeks_with_an_alarm"]
            agg["episodes"] += f["episodes"]
            agg["years_checked"] = round(agg["years_checked"] + f["years_checked"], 2)
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
    """Design on a world built by the same generator the fresh worlds will use (decision 99)."""
    from cpgos.finance.world import GENERATOR_VERSION
    t = time.time()
    side = None
    if os.environ.get("WORLD") == "side":
        from sidecar_world import sibling
        side = psycopg.connect(sibling(config.get("CPGOS_DATABASE_URL"))[0], autocommit=True)
        migrate(side)
    try:
        w = sc.load(side or conn)
    finally:
        if side:
            side.close()
    policy = drift.Policy()
    fa, det = run([w], sc.SET1, policy, ROLLS)
    quarters = sc.netted_share_by_quarter(w)
    rid = _save(conn, "drift_design", {"policy": policy.__dict__, "rolls": ROLLS, "false_alarms": fa, "detection": det,
                                       "world": "side" if os.environ.get("WORLD") == "side" else "main",
                                       "generator": w.generator, "netted_share_by_quarter": quarters,
                                       "reason_wordings": "development set (unseen set 1)"})
    print(f"{'side' if os.environ.get('WORLD') == 'side' else 'main'} world (generator {w.generator}; fresh worlds "
          f"use {GENERATOR_VERSION}), {drift.DRIFT_VERSION}, {ROLLS} rolls per change")
    print("   short-pay share by quarter: " + ", ".join(f"{q['quarter']} {q['netted_share']:.0%}" for q in quarters))
    _print(fa, det)
    print(f"report {rid} (kind 'drift_design')  {time.time() - t:.0f}s")
    return 0


def _build(conn, seeds, label: str, cache: Path | None = None) -> list[sc.World]:
    """Build each seed's world in a copy of the M5 base database, load its streams, drop the copy.
    With cache, a world built before is read back instead (development worlds only)."""
    import pickle
    from urllib.parse import urlsplit
    from agent_seeds import _parquet, _url
    from cpgos.finance.world import GENERATOR_VERSION, WorldConfig, build_world, weekly_sales_from_parquet
    from cpgos.ingest.reference import load_deduction_codes
    url = config.get("CPGOS_DATABASE_URL")
    main_db = urlsplit(url).path.lstrip("/")
    base, admin = f"{main_db}_m5base", _url(url, "postgres")
    with psycopg.connect(admin, autocommit=True) as a:
        if not a.execute("SELECT 1 FROM pg_database WHERE datname = %s", (base,)).fetchone():
            raise SystemExit(f"no base database {base}: run make agent-seeds once (it loads M5 into it), then this")
    pq, worlds = _parquet(), []
    for ws in seeds:
        f = cache / f"world_{ws}_gen{GENERATOR_VERSION}.pkl" if cache else None
        if f and f.exists():
            worlds.append(pickle.loads(f.read_bytes()))
            continue
        db, t0 = f"{main_db}_{label}{ws}", time.time()
        with psycopg.connect(admin, autocommit=True) as a:
            a.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
            a.execute(f'CREATE DATABASE "{db}" TEMPLATE "{base}"')
        try:
            with psycopg.connect(_url(url, db), autocommit=True) as c:
                migrate(c)
                cfg = WorldConfig(seed=ws)
                load_deduction_codes(c)
                build_world(c, weekly_sales_from_parquet(pq, cfg.weeks, cfg.n_skus, cfg.seed), cfg,
                            source_note=f"{label} seed {ws}")
                worlds.append(sc.load(c))
            if f:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(pickle.dumps(worlds[-1]))
            print(f"world {ws}: {len(worlds[-1].lines)} cash lines, {len(worlds[-1].deductions)} claims, "
                  f"{len(worlds[-1].prices)} price rows  {time.time() - t0:.0f}s", flush=True)
        finally:
            with psycopg.connect(admin, autocommit=True) as a:
                a.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
    return worlds


def choose(results: dict[str, dict]) -> str | None:
    """Written before any 1.2 result (decision 100). Eligible: every change caught in at least 95% of
    rolls within 120 days. Among those, the fewest false alarms per world-year on the worst stream, which
    must be at most 1; ties go to the faster median detection (sum over changes). None if nothing qualifies."""
    ok = []
    for name, r in results.items():
        if all(d["rolls"] and d["detected"] / d["rolls"] >= 0.95 for d in r["detection"].values()):
            worst = max(f["episodes"] / max(f["years_checked"], 1e-9) for f in r["false_alarms"].values())
            speed = sum(d["days_to_alarm"]["median"] or 999 for d in r["detection"].values())
            if worst <= 1.0:
                ok.append((worst, speed, name))
    return min(ok)[2] if ok else None


def cmd_dev(conn) -> int:
    from cpgos.finance.world import GENERATOR_VERSION
    t = time.time()
    worlds = _build(conn, DEV_SEEDS, "driftdev", ROOT / "local_runs" / "drift_dev")
    rolls = max(2, ROLLS // 4)
    results = {}
    for name, pol in drift.CANDIDATES.items():
        fa, det = run(worlds, sc.SET1, pol, rolls)
        results[name] = {"policy": pol.__dict__, "false_alarms": fa, "detection": det}
        print(f"\n candidate {name}")
        _print(fa, det)
    chosen = choose(results)
    rid = _save(conn, "drift_dev", {"seeds": list(DEV_SEEDS), "generator": GENERATOR_VERSION, "rolls_per_world": rolls,
                                    "rule": choose.__doc__.split("(decision 100). ")[1].strip(),
                                    "candidates": results, "chosen": chosen})
    print(f"\nchosen by the rule written beforehand: {chosen}   (report {rid}, kind 'drift_dev')  {time.time() - t:.0f}s")
    return 0 if chosen else 2


def cmd_fresh(conn) -> int:
    from cpgos.finance.world import GENERATOR_VERSION
    done = conn.execute("SELECT report_id FROM eval.report WHERE kind = 'drift_fresh' AND code_version = %s",
                        (drift.DRIFT_VERSION,)).fetchone()
    if done:
        print(f"already scored for {drift.DRIFT_VERSION} (report {done[0]}); fresh worlds are scored once per version")
        return 0
    dev = conn.execute("""SELECT report_id, body->>'generator', body->>'chosen' FROM eval.report WHERE kind = 'drift_dev'
                          AND code_version = %s ORDER BY report_id DESC LIMIT 1""", (drift.DRIFT_VERSION,)).fetchone()
    if not dev or dev[1] != GENERATOR_VERSION or not dev[2]:
        print(f"refusing: fresh worlds use generator {GENERATOR_VERSION}; run make drift-dev first (its worlds must use "
              f"the same generator and its rule must pick a setting). Latest: {dev}")
        return 1
    policy = drift.CANDIDATES[dev[2]]
    worlds = _build(conn, FRESH_SEEDS, "drift")
    fa, det = run(worlds, sc.SET2, policy, max(2, ROLLS // 4))
    rid = _save(conn, "drift_fresh", {"policy": policy.__dict__, "candidate": dev[2], "dev_report": dev[0],
                                      "seeds": list(FRESH_SEEDS), "generator": GENERATOR_VERSION,
                                      "false_alarms": fa, "detection": det, "reason_wordings": "unseen set 2"})
    print(f"\n{len(worlds)} fresh worlds, {drift.DRIFT_VERSION} ({dev[2]}, chosen in report {dev[0]}), unseen reason wordings")
    _print(fa, det)
    print(f"report {rid} (kind 'drift_fresh'), scored once for {drift.DRIFT_VERSION}")
    return 0


def cmd_watch(conn) -> int:
    from cpgos.monitor import alarms
    new = alarms.watch(conn)
    opened = alarms.open_alarms(conn)
    print(f"{len(new)} new alarm(s); {len(opened)} open")
    for a in opened:
        print(f"   #{a['drift_alarm_id']} {a['stream']}/{a['feature']} through {a['checked_through']}: "
              f"p {a['p_value']:.1e}, PSI {a['psi']:.2f}, moved {a['moved'][:2]}")
    if any(a["stream"] == "cash" for a in opened):
        print("   a cash alarm is open: every would-be automatic posting goes to a person until it is cleared")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["design", "dev", "fresh", "watch"])
    args = ap.parse_args()
    with connect() as conn:
        conn.autocommit = True
        migrate(conn)
        return {"design": cmd_design, "dev": cmd_dev, "fresh": cmd_fresh, "watch": cmd_watch}[args.command](conn)


if __name__ == "__main__":
    sys.exit(main())
