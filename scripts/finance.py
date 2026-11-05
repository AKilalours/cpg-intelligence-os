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
    sp = ca["by_noise"].get("deducted_at_payment")
    if sp:
        print(f"                  short-pays explained on the line: {sp['n']}, posted automatically and right {sp['auto_correct']},"
              f" wrong {sp['auto_wrong']}, sent to a person {sp['review']}")
    print(f"                  held for a person because another open invoice has the same amount: {ca.get('held_for_twin_amount', 0)}")
    dup = d["by_reason"].get("duplicate")
    if dup:
        print(f"deductions        duplicates in the answer key: {dup['n']}, rejected {dup['verdict_correct']},"
              f" linked to the earlier claim {dup['named_duplicate']}, needs evidence {dup['needs_evidence']}")
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


def cmd_agent_design(conn) -> int:
    """Phase 3, design region (California) only: where the agent differs from the engine, with the truth.
    Safe to rerun while building playbooks; the scoring region is not shown."""
    from cpgos.agent import investigate as inv
    from cpgos.agent.score import score
    s = score(conn, inv.investigate(conn))
    d = s["design_region"]
    print(json.dumps({"agent": s["agent"], "design_region": d,
                      "agent_differs_from_engine": [x for x in s["agent_differs_from_engine"] if x["region"] == "CA"]},
                     indent=1, default=str))
    return 0


def cmd_agent_score(conn) -> int:
    """Phase 3, scored once on Texas and Wisconsin per agent version; the cases and reasoning are saved."""
    from psycopg.types.json import Jsonb
    from cpgos.agent import investigate as inv
    from cpgos.agent.score import score
    done = conn.execute("SELECT report_id FROM eval.report WHERE kind = 'agent' AND body->>'agent' = %s",
                        (inv.AGENT_VERSION,)).fetchone()
    if done:
        print(f"{inv.AGENT_VERSION} was already scored (report {done[0]}). A playbook change needs a new version;"
              " scoring the same version twice would let it be tuned on the scoring region.")
        return 1
    findings = inv.investigate(conn)
    inv.save(conn, findings)
    s = score(conn, findings)
    rid = conn.execute("INSERT INTO eval.report (kind, code_version, body) VALUES ('agent', %s, %s) RETURNING report_id",
                       (code_version(), Jsonb(json.loads(json.dumps(s, default=str))))).fetchone()[0]
    r = s["report_region"]
    print(f"report {rid}: {inv.AGENT_VERSION} on {'+'.join(r['regions'])}, {r['claims']} claims")
    print(f"  actions {r['actions']}")
    print(f"  false disputes   agent {r['false_disputes']['k']}/{r['false_disputes']['n']}"
          f"   engine {r['engine']['false_disputes']['k']}/{r['engine']['false_disputes']['n']}")
    print(f"  false approvals  agent {r['false_approvals']['k']}/{r['false_approvals']['n']}"
          f"   engine {r['engine']['false_approvals']['k']}/{r['engine']['false_approvals']['n']}")
    print(f"  claims left undecided: engine {r['engine']['needs_evidence']}, agent requests {r['requests']['n']}"
          f" (for claims that were valid {r['requests']['claim_was_valid']}, invalid {r['requests']['claim_was_invalid']})")
    print(f"  disputes exact to the cent {r['disputes_exact_to_the_cent']['k']}/{r['disputes_exact_to_the_cent']['n']},"
          f" recoverable disputed correctly ${r['recoverable_disputed_correctly']} of ${r['true_recoverable_total']}")
    for x in s["agent_differs_from_engine"]:
        if x["region"] in r["regions"]:
            print(f"  differs: {x}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["world", "reconcile", "evaluate", "evidence", "restore-evidence",
                                        "agent-design", "agent-score"])
    args = ap.parse_args()
    with connect() as conn:
        conn.autocommit = True
        migrate(conn)
        return {"world": cmd_world, "reconcile": cmd_reconcile, "evaluate": cmd_evaluate,
                "evidence": cmd_evidence, "restore-evidence": cmd_restore_evidence,
                "agent-design": cmd_agent_design, "agent-score": cmd_agent_score}[args.command](conn)


if __name__ == "__main__":
    sys.exit(main())
