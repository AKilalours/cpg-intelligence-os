#!/usr/bin/env python3
"""The frozen agent against the engine on SEEDS fresh worlds (default 8), compared claim by claim. $0.

    make agent-seeds              # 8 worlds; the first run loads M5 once into a base database (~9 min)
    make agent-seeds SEEDS=12

The main database is never touched except to store the pooled report. M5 weekly sales go into one base
database once; each world is a copy of it (seconds), built with its own world and evidence seeds, scored,
and dropped again to free disk. Seeds start at 101 (worlds) and 2001 (evidence): none is the main world's.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

from cpgos import config  # noqa: E402
from cpgos.agent import investigate as inv  # noqa: E402
from cpgos.agent import seeds  # noqa: E402
from cpgos.db import code_version, wait_until_ready  # noqa: E402
from cpgos.finance.evaluate import REPORT_REGIONS  # noqa: E402
from cpgos.finance.world import WorldConfig, weekly_sales_from_parquet  # noqa: E402


def _url(url: str, db: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, "/" + db, p.query, p.fragment))


def _parquet() -> Path:
    found = [p for p in sorted((ROOT / "data/processed/m5/real").glob("*/")) if any(p.glob("store_id=*"))]
    if len(found) != 1:
        raise SystemExit("Expected exactly one M5 Parquet version under data/processed/m5/real/ (make load-m5)")
    return found[0]


def main() -> int:
    url = config.get("CPGOS_DATABASE_URL")
    main_db = urlsplit(url).path.lstrip("/")
    base, admin = f"{main_db}_m5base", _url(url, "postgres")
    n = int(os.environ.get("SEEDS") or 8)
    wait_until_ready(admin)
    with psycopg.connect(admin, autocommit=True) as a:
        have = a.execute("SELECT 1 FROM pg_database WHERE datname = %s", (base,)).fetchone()
    if not have:
        print(f"creating base database {base} with M5 weekly sales (once, about 9 minutes)", flush=True)
        with psycopg.connect(admin, autocommit=True) as a:
            a.execute(f'CREATE DATABASE "{base}"')
        env = {**os.environ, "CPGOS_DATABASE_URL": _url(url, base)}
        for step in ("migrate", "load-m5"):
            if subprocess.run(["make", step], env=env).returncode != 0:
                with psycopg.connect(admin, autocommit=True) as a:
                    a.execute(f'DROP DATABASE IF EXISTS "{base}" WITH (FORCE)')
                print(f"stopped at make {step}; the base database was removed")
                return 1
    pq = _parquet()
    rows: list[dict] = []
    for i in range(n):
        ws, es = 101 + i, 2001 + i
        db = f"{main_db}_seed{ws}"
        t0 = time.time()
        with psycopg.connect(admin, autocommit=True) as a:
            a.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
            a.execute(f'CREATE DATABASE "{db}" TEMPLATE "{base}"')
        try:
            cfg = WorldConfig(seed=ws)
            sales = weekly_sales_from_parquet(pq, cfg.weeks, cfg.n_skus, cfg.seed)
            with psycopg.connect(_url(url, db), autocommit=True) as c:
                got = seeds.run_world(c, sales, ws, es)
            rows += got
            p = seeds.pool(got)
            print(f"world {i + 1}/{n} (seeds {ws}, {es}): {p['claims']} claims, false disputes engine "
                  f"{p['engine']['false_disputes']['k']}/{p['engine']['false_disputes']['n']}, agent "
                  f"{p['agent']['false_disputes']['k']}/{p['agent']['false_disputes']['n']}  {time.time() - t0:.0f}s",
                  flush=True)
        finally:
            if os.environ.get("KEEP") != "1":
                with psycopg.connect(admin, autocommit=True) as a:
                    a.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
    body = {"version": seeds.SEEDS_VERSION, "agent": inv.AGENT_VERSION, "worlds": n,
            "seeds": {"world": [101 + i for i in range(n)], "evidence": [2001 + i for i in range(n)]},
            "all_regions": seeds.pool(rows), "report_regions": seeds.pool(rows, REPORT_REGIONS), "claims": rows}
    with psycopg.connect(url, autocommit=True) as c:
        rid = c.execute("INSERT INTO eval.report (kind, code_version, body) VALUES ('agent_seeds', %s, %s) RETURNING report_id",
                        (code_version(), Jsonb(json.loads(json.dumps(body, default=str))))).fetchone()[0]
    a_ = body["all_regions"]
    e, g, pd, pa = a_["engine"], a_["agent"], a_["paired"]["false_disputes"], a_["paired"]["false_approvals"]
    print(f"\nreport {rid}: {inv.AGENT_VERSION} on {n} fresh worlds, {a_['claims']} claims, all new to both")
    print(f"  valid claims disputed   engine {e['false_disputes']['k']}/{e['false_disputes']['n']} {e['false_disputes']['ci95']}"
          f"   agent {g['false_disputes']['k']}/{g['false_disputes']['n']} {g['false_disputes']['ci95']}")
    print(f"  invalid claims approved engine {e['false_approvals']['k']}/{e['false_approvals']['n']}"
          f"   agent {g['false_approvals']['k']}/{g['false_approvals']['n']}")
    print(f"  same claims, wrong for only one: disputes engine-only {pd['engine_only']}, agent-only {pd['agent_only']}"
          f" (exact McNemar p = {pd['p_value']}); approvals engine-only {pa['engine_only']}, agent-only {pa['agent_only']}")
    print(f"  the engine's false disputes became: {a_['engine_false_disputes_became']}")
    print(f"  cost: real over-claims the agent asked about instead of disputing: "
          f"{a_['invalid_claims_agent_asked_about_instead_of_disputing']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
