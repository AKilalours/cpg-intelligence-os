"""Phase 2a commands: build the financial world, reconcile it, evaluate the engines.

Usage:
    python scripts/finance.py world       # build the real-anchored world from the loaded M5 Parquet (once per database)
    python scripts/finance.py reconcile   # cash application + deduction validation, written as cpgos_engine
    python scripts/finance.py evaluate    # replay both engines, score against eval.*, store the report
    python scripts/finance.py evidence    # hide or mis-key evidence realistically, once (see cpgos/finance/evidence.py)
    python scripts/finance.py restore-evidence  # repair records that evidence-1.0.0 altered (verified, all or nothing)

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

from cpgos.db import code_version, connect, database_url, migrate  # noqa: E402
from cpgos.finance import cashapp, deductions, evidence  # noqa: E402
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


def cmd_evidence(conn) -> int:
    print(json.dumps(evidence.degrade(conn), indent=1, default=str))
    return 0


def cmd_restore_evidence(conn) -> int:
    """Undo evidence-1.0.0's changes to the records, verified against a rebuilt world."""
    cfg = WorldConfig()
    pq = _parquet_dir()

    def rebuild(tmp):
        load_deduction_codes(tmp)
        build_world(tmp, weekly_sales_from_parquet(pq, cfg.weeks, cfg.n_skus, cfg.seed), cfg,
                    source_note=f"M5 parquet {pq.name}")
    print(json.dumps(evidence.restore_v1(conn, database_url(), rebuild), indent=1, default=str))
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
    for name in ("design_region", "report_region"):
        b = d[name]
        print(f"deductions {name} {'+'.join(b['regions'])}: {b['deductions']} claims, needs evidence {b['needs_evidence']['k']},"
              f" false approvals {b['false_approval']['k']}/{b['false_approval']['n']},"
              f" false disputes {b['false_dispute']['k']}/{b['false_dispute']['n']},"
              f" exact when decided {b['recoverable_exact_to_the_cent_when_decided']['k']}/{b['recoverable_exact_to_the_cent_when_decided']['n']}")
    for m in d["mistakes"]:
        print(f"  mistake: {m}")
    for row in report["recovery_simulation"]:
        print(f"recovery @ {row['reviews_per_week']}/week  engine ${row['engine']['recovered']}"
              f"  largest-first ${row['largest_amount']['recovered']}  oldest-first ${row['oldest_first']['recovered']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["world", "reconcile", "evaluate", "evidence", "restore-evidence"])
    args = ap.parse_args()
    with connect() as conn:
        conn.autocommit = True
        migrate(conn)
        return {"world": cmd_world, "reconcile": cmd_reconcile, "evaluate": cmd_evaluate,
                "evidence": cmd_evidence, "restore-evidence": cmd_restore_evidence}[args.command](conn)


if __name__ == "__main__":
    sys.exit(main())
