"""Load a downloaded dataset into the database.

Usage:
    python scripts/load_data.py m5                      # all 10 stores, weekly to Postgres, daily to Parquet
    python scripts/load_data.py m5 --stores CA_1,CA_2   # partial load (recorded as partial)
    python scripts/load_data.py kehe_deduction_codes

Requires CPGOS_DATABASE_URL and an accepted license in data/license_acceptance.yaml.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cpgos.datasets.registry import LicenseNotAcceptedError, load_registry, pin_or_verify, require_license_accepted  # noqa: E402
from cpgos.db import connect, migrate  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", choices=["m5", "kehe_deduction_codes"])
    ap.add_argument("--stores", help="comma-separated store codes for a partial load, e.g. CA_1,TX_2")
    ap.add_argument("--granularity", default="week", choices=["day", "week", "none"],
                    help="what goes into Postgres (daily rows always go to Parquet)")
    ap.add_argument("--memory-limit", default="2GB")
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

        from cpgos.ingest.m5 import load_m5
        for name in spec.raw["files"]:
            f = spec.raw_dir / name
            if not f.exists():
                print(f"MISSING: {f}. Run: python scripts/download_data.py m5")
                return 1
            pin_or_verify("m5", f)  # refuse silently changed files
        rep = load_m5(
            conn, spec.raw_dir, origin=spec.origin, license_=spec.license,
            stores=args.stores.split(",") if args.stores else None,
            pg_granularity=args.granularity, memory_limit=args.memory_limit, threads=args.threads,
        )
        out = {k: v for k, v in rep.__dict__.items()}
        out["seconds"] = round(time.time() - t0, 1)
        print(json.dumps(out, indent=2, default=str))
        return 0 if rep.status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
