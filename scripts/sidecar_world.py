"""Score a new financial-world generator in a side database, never in the main one.

The main database holds one world plus the document work (decisions, reviews, and recorded model
answers that cost money), and a world can only be built once per database. So a new generator is
scored next door: this creates `<main name>_world12` on the same server, migrates it, loads M5,
builds the world, reconciles, and evaluates, all with the side database's address passed to the
usual make targets. The address is derived from CPGOS_DATABASE_URL and never printed.

    make world-sidecar            # build and score once
    make world-sidecar FRESH=1    # drop and rebuild the side database first (side database only)
"""

from __future__ import annotations

import os
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit

SUFFIX = "_world12"
STEPS = ["migrate", "load-m5", "finance-world", "reconcile", "eval-recon"]


def sibling(url: str, suffix: str = SUFFIX) -> tuple[str, str, str]:
    """(side database URL, admin URL on the same server, side database name). Raises if it would be the main one."""
    parts = urlsplit(url)
    main = parts.path.lstrip("/")
    if not main:
        raise ValueError("CPGOS_DATABASE_URL names no database")
    side = main + suffix
    if side == main or not side.endswith(suffix):
        raise ValueError("refusing: the side database would be the main database")
    mk = lambda db: urlunsplit((parts.scheme, parts.netloc, "/" + db, parts.query, parts.fragment))
    return mk(side), mk("postgres"), side


def main() -> int:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    import psycopg
    from cpgos import config
    from cpgos.db import wait_until_ready
    url = config.get("CPGOS_DATABASE_URL")
    if not url:
        print("CPGOS_DATABASE_URL is not set: run make setup")
        return 1
    side_url, admin_url, side = sibling(url)
    wait_until_ready(admin_url)
    with psycopg.connect(admin_url, autocommit=True) as a:
        exists = a.execute("SELECT 1 FROM pg_database WHERE datname = %s", (side,)).fetchone()
        if exists and os.environ.get("FRESH") == "1":
            a.execute(f'DROP DATABASE "{side}" WITH (FORCE)')
            exists = None
            print(f"dropped side database {side}")
        if exists:
            print(f"side database {side} already exists; rerun with FRESH=1 to rebuild it (the main database is never touched)")
            return 1
        a.execute(f'CREATE DATABASE "{side}"')
    print(f"side database {side} created; the main database is untouched")
    env = {**os.environ, "CPGOS_DATABASE_URL": side_url}
    for step in STEPS:
        print(f"--- make {step} (side database)", flush=True)
        if subprocess.run(["make", step], env=env).returncode != 0:
            print(f"stopped at make {step}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
