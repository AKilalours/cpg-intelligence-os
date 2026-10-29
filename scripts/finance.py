"""Phase 2a commands: build the financial world, reconcile it, evaluate the engines.

Usage:
    python scripts/finance.py world       # build the real-anchored world from the loaded M5 Parquet (once per database)
    python scripts/finance.py reconcile   # cash application + deduction validation, written as cpgos_engine
    python scripts/finance.py evaluate    # replay both engines, score against eval.*, store the report

No LLM is called by any of these. Requires CPGOS_DATABASE_URL and a completed `make load-m5`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cpgos.preflight import require_packages  # noqa: E402  (stdlib only)

require_packages()

from cpgos.db import code_version, connect, migrate  # noqa: E402
from cpgos.finance import cashapp, deductions  # noqa: E402
from cpgos.finance.evaluate import phase2a_report, save_report  # noqa: E402
from cpgos.finance.world import WorldConfig, build_world, weekly_sales_from_parquet  # noqa: E402
from cpgos.ingest.reference import load_deduction_codes  # noqa: E402


def _parquet_dir() -> Path:
    found = sorted((ROOT / "data/processed/m5/real").glob("*/"))
    found = [p for p in found if any(p.glob("store_id=*"))]
    if not found:
        raise SystemExit("No real M5 Parquet found under data/processed/m5/real/. Run: make load-m5")
    if len(found) > 1:
        raise SystemExit(f"Several M5 Parquet versions found; keep one: {[p.name for p in found]}")
    return found[0]


def cmd_world(conn) -> int:
    if conn.execute("SELECT count(*) FROM dataset_version WHERE dataset_key = 'finance_world'").fetchone()[0]:
        print("A financial world already exists in this database. Nothing to do.")
        print("To regenerate, use a fresh database (one world per database keeps the answer key unambiguous).")
        return 0
    cfg = WorldConfig()
    t = time.time()
    pq = _parquet_dir()
    sales = weekly_sales_from_parquet(pq, cfg.weeks, cfg.n_skus, cfg.seed)
    load_deduction_codes(conn)
    rep = build_world(conn, sales, cfg, source_note=f"M5 parquet {pq.name}")
    print(json.dumps({"as_of": str(rep.as_of), "invoices": rep.invoices, "invoice_lines": rep.invoice_lines,
                      "promotions": rep.promotions, "payments": rep.payments, "remittance_lines": rep.remittance_lines,
                      "deductions": rep.deductions, "deductions_pending": rep.deductions_pending,
                      "noise": rep.noise_counts, "seconds": round(time.time() - t, 1)}, indent=1))
    return 0


def cmd_reconcile(conn) -> int:
    matches = cashapp.match_all(conn, "full")
    counts = cashapp.apply(conn, matches)
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_engine")
        assessed = deductions.assess_all(conn)
    written = deductions.save(conn, assessed)
    verdicts: dict[str, int] = {}
    for a in assessed:
        verdicts[a.verdict] = verdicts.get(a.verdict, 0) + 1
    print(json.dumps({"cash_application": counts, "deductions_assessed": len(assessed),
                      "assessments_written": written, "verdicts": verdicts}, indent=1))
    return 0


def cmd_evaluate(conn) -> int:
    report = phase2a_report(conn)
    rid = save_report(conn, report, code_version())
    ca, base, d = report["cash_application"]["full"], report["cash_application"]["exact_only_baseline"], report["deductions"]
    print(f"report {rid} on world as of {report['world']['as_of']}")
    print(f"cash application  wrong automatic postings: full {ca['false_match_rate']['k']}/{ca['auto_applied']['k']}"
          f"  baseline {base['false_match_rate']['k']}/{base['auto_applied']['k']}")
    print(f"                  dollars posted correctly without a person: full {ca['dollars_auto_applied_correctly']['share']}"
          f"  baseline {base['dollars_auto_applied_correctly']['share']}")
    print(f"deductions        recoverable exact to the cent: {d['recoverable_exact_to_the_cent']['k']}/{d['deductions']}"
          f"  false approvals {d['false_approval']['k']}/{d['false_approval']['n']}"
          f"  false disputes {d['false_dispute']['k']}/{d['false_dispute']['n']}")
    for row in report["recovery_simulation"]:
        print(f"recovery @ {row['reviews_per_week']}/week  engine ${row['engine']['recovered']}"
              f"  largest-first ${row['largest_amount']['recovered']}  oldest-first ${row['oldest_first']['recovered']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["world", "reconcile", "evaluate"])
    args = ap.parse_args()
    with connect() as conn:
        conn.autocommit = True
        migrate(conn)
        return {"world": cmd_world, "reconcile": cmd_reconcile, "evaluate": cmd_evaluate}[args.command](conn)


if __name__ == "__main__":
    sys.exit(main())
