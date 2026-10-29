"""Load a downloaded dataset into the database.

Usage:
    python scripts/load_data.py m5                      # all stores, one at a time (resumable)
    python scripts/load_data.py m5 --stores CA_1,CA_2   # partial load (recorded as partial)
    python scripts/load_data.py kehe_deduction_codes

Requires CPGOS_DATABASE_URL and an accepted license in data/license_acceptance.yaml.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cpgos.preflight import require_packages  # noqa: E402  (stdlib only)

require_packages()  # clear message instead of an import traceback when run with the wrong Python

from cpgos.datasets.registry import LicenseNotAcceptedError, load_registry, pin_or_verify, require_license_accepted  # noqa: E402
from cpgos.db import connect, migrate  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", choices=["m5", "kehe_deduction_codes"])
    ap.add_argument("--stores", help="comma-separated store codes for a partial load, e.g. CA_1,TX_2")
    ap.add_argument("--granularity", default="week", choices=["day", "week", "none"],
                    help="what goes into Postgres (daily rows always go to Parquet)")
    ap.add_argument("--single-pass", action="store_true",
                    help="load all stores in one run instead of store by store (needs more memory)")
    ap.add_argument("--force", action="store_true", help="reload stores even if already loaded from this file")
    ap.add_argument("--memory-limit", default="1GB")
    ap.add_argument("--threads", type=int, default=4)
    args = ap.parse_args()

    spec = load_registry()[args.dataset]
    try:
        require_license_accepted(spec)
    except LicenseNotAcceptedError as e:
        print(f"BLOCKED: {e}")
        return 1

    with connect() as conn:
        conn.autocommit = True
        migrate(conn)
        t0 = time.time()
        if args.dataset == "kehe_deduction_codes":
            from cpgos.ingest.reference import load_deduction_codes
            print(f"loaded {load_deduction_codes(conn)} deduction codes")
            return 0

        from cpgos.ingest.m5 import LoadInProgressError, load_m5, load_m5_by_store
        for name in spec.raw["files"]:
            f = spec.raw_dir / name
            if not f.exists():
                print(f"MISSING: {f}. Run: make download DATASET=m5")
                return 1
            pin_or_verify("m5", f)  # refuse silently changed files

        common = dict(origin=spec.origin, license_=spec.license, pg_granularity=args.granularity,
                      memory_limit=args.memory_limit, threads=args.threads)
        stores = args.stores.split(",") if args.stores else None
        try:
            if args.single_pass:
                print("Single-pass load: all stores in one run (not the verified default).")
                reports = [load_m5(conn, spec.raw_dir, stores=stores, **common)]
            else:
                reports = load_m5_by_store(conn, spec.raw_dir, stores=stores, force=args.force, **common)
        except LoadInProgressError as e:
            print(f"BLOCKED: {e}")
            return 1

        ok = all(r.status == "succeeded" for r in reports)
        rows = sum(r.rows_in for r in reports)
        print(f"\n{'Done' if ok else 'Stopped'}: {len(reports)} run(s), {rows:,} rows validated, "
              f"{sum(r.rows_quarantined for r in reports):,} quarantined, {time.time() - t0:,.0f}s.")
        if not reports:
            print("Nothing to do: every store was already loaded from this exact file (use --force to reload).")
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
