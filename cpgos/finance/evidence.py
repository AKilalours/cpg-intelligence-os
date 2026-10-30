"""Realistic evidence for deduction checks: gaps, restatements, and typos.

Why this exists: the financial world creates claims from true events and
records the evidence (retailer POS, receipts, shipments, the promotion
calendar) perfectly. A checker reading perfect evidence produced by the same
rules can only score 100%, which proves the arithmetic and nothing else.

Analogy: grading a student with the answer key they were taught from. Real
files are messier: the retailer's sales feed misses a week, a receipt is
never scanned, a delivery date is not keyed in, the promotion calendar has a
promotion missing or ends a week off, and a claim arrives without its store.

This step damages only what the checker can see, with its own random seed,
after the world is built: invoices, payments, cash application, and the
answer key are unchanged. Every change is logged in eval.gt_evidence so the
cost of each kind of mess can be measured. It runs once per world (it is
recorded in finance_evidence and later runs are a no-op).

The rates are assumptions, each a parameter of EvidenceConfig and listed in
docs/phase2a_reconciliation.md.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass

import psycopg
from psycopg.types.json import Jsonb

EVIDENCE_VERSION = "evidence-1.0.0"


@dataclass(frozen=True)
class EvidenceConfig:
    seed: int = 1709                       # independent of the world's seed: the world itself is unchanged
    p_pos_week_missing: float = 0.03       # retailer POS feed has no row for the week
    p_pos_week_restated: float = 0.05      # retailer later restated the week: units off by 1 to 3%
    p_receipt_missing: float = 0.04        # receiving never scanned the line
    p_delivery_date_missing: float = 0.03  # shipment delivered, date not keyed
    p_promo_missing: float = 0.02          # promotion agreed, never entered in the calendar
    p_promo_end_shifted: float = 0.04      # promotion end date keyed a week off
    p_claim_store_missing: float = 0.03    # claim arrives without the store


def degrade(conn: psycopg.Connection, cfg: EvidenceConfig = EvidenceConfig()) -> dict:
    """Apply the evidence damage to the database's financial world, once."""
    ver = conn.execute("""SELECT d.dataset_version_id FROM dataset_version d WHERE d.dataset_key = 'finance_world'
                          ORDER BY d.dataset_version_id DESC LIMIT 1""").fetchone()
    if ver is None:
        raise RuntimeError("no financial world in this database; run: make finance-world")
    ver = ver[0]
    done = conn.execute("SELECT report FROM finance_evidence WHERE dataset_version_id = %s AND version = %s",
                        (ver, EVIDENCE_VERSION)).fetchone()
    if done:
        return {**done[0], "already_applied": True}
    rng = random.Random(cfg.seed)
    log = []
    counts = {}

    def pick(rows, p):
        return [r for r in rows if rng.random() < p]

    with conn.transaction():
        # Order every query: the same world must always be damaged the same way.
        pos = conn.execute("""SELECT store_id, sku_id, week_start, units FROM retailer_pos_week
                              WHERE dataset_version_id = %s ORDER BY 1, 2, 3""", (ver,)).fetchall()
        missing = pick(pos, cfg.p_pos_week_missing)
        for st, sk, wk, u in missing:
            conn.execute("DELETE FROM retailer_pos_week WHERE store_id = %s AND sku_id = %s AND week_start = %s", (st, sk, wk))
            log.append(("pos_week_missing", f"{st}/{sk}/{wk}", {"units": u}))
        gone = {(r[0], r[1], r[2]) for r in missing}
        restated = pick([r for r in pos if (r[0], r[1], r[2]) not in gone and r[3] > 0], cfg.p_pos_week_restated)
        for st, sk, wk, u in restated:
            new = max(0, round(u * (1 + rng.choice([-1, 1]) * rng.uniform(0.01, 0.03))))
            if new == u:
                new = u + 1
            conn.execute("UPDATE retailer_pos_week SET units = %s WHERE store_id = %s AND sku_id = %s AND week_start = %s",
                         (new, st, sk, wk))
            log.append(("pos_week_restated", f"{st}/{sk}/{wk}", {"true_units": u, "reported_units": new}))
        counts["pos_weeks"] = {"total": len(pos), "missing": len(missing), "restated": len(restated)}

        rec = conn.execute("""SELECT r.invoice_line_id FROM receipt_line r JOIN invoice_line il USING (invoice_line_id)
                              JOIN invoice i USING (invoice_id) WHERE i.dataset_version_id = %s ORDER BY 1""", (ver,)).fetchall()
        lost = pick(rec, cfg.p_receipt_missing)
        for (lid,) in lost:
            conn.execute("DELETE FROM receipt_line WHERE invoice_line_id = %s", (lid,))
            log.append(("receipt_missing", str(lid), {}))
        counts["receipt_lines"] = {"total": len(rec), "missing": len(lost)}

        ship = conn.execute("SELECT shipment_id FROM shipment WHERE dataset_version_id = %s ORDER BY 1", (ver,)).fetchall()
        undated = pick(ship, cfg.p_delivery_date_missing)
        for (sid,) in undated:
            conn.execute("UPDATE shipment SET delivered_date = NULL WHERE shipment_id = %s", (sid,))
            log.append(("delivery_date_missing", str(sid), {}))
        counts["shipments"] = {"total": len(ship), "undated": len(undated)}

        promos = conn.execute("""SELECT promotion_id, start_date, end_date FROM promotion
                                 WHERE dataset_version_id = %s ORDER BY 1""", (ver,)).fetchall()
        dropped = pick(promos, cfg.p_promo_missing)
        for pid, s, e in dropped:
            conn.execute("DELETE FROM promotion WHERE promotion_id = %s", (pid,))      # promotion_sku cascades
            log.append(("promo_missing", str(pid), {"start": str(s), "end": str(e)}))
        kept = [p for p in promos if p[0] not in {d[0] for d in dropped}]
        shifted = pick(kept, cfg.p_promo_end_shifted)
        for pid, s, e in shifted:
            from datetime import timedelta
            new_end = e + timedelta(days=rng.choice([-7, 7]))
            if new_end < s:
                new_end = e + timedelta(days=7)
            conn.execute("UPDATE promotion SET end_date = %s WHERE promotion_id = %s", (new_end, pid))
            log.append(("promo_end_shifted", str(pid), {"true_end": str(e), "keyed_end": str(new_end)}))
        counts["promotions"] = {"total": len(promos), "missing": len(dropped), "end_shifted": len(shifted)}

        deds = conn.execute("""SELECT deduction_id FROM deduction WHERE dataset_version_id = %s AND store_id IS NOT NULL
                               ORDER BY 1""", (ver,)).fetchall()
        storeless = pick(deds, cfg.p_claim_store_missing)
        for (did,) in storeless:
            st = conn.execute("SELECT store_id FROM deduction WHERE deduction_id = %s", (did,)).fetchone()[0]
            conn.execute("UPDATE deduction SET store_id = NULL WHERE deduction_id = %s", (did,))
            log.append(("claim_store_missing", str(did), {"store_id": st}))
        counts["claims"] = {"with_store": len(deds), "store_removed": len(storeless)}

        with conn.cursor().copy("COPY eval.gt_evidence (dataset_version_id, kind, ref, detail) FROM STDIN") as cp:
            for kind, ref, detail in log:
                cp.write_row((ver, kind, ref, Jsonb(detail)))
        report = {"version": EVIDENCE_VERSION, "config": asdict(cfg), "changes": len(log), **counts}
        conn.execute("INSERT INTO finance_evidence (dataset_version_id, version, report) VALUES (%s, %s, %s)",
                     (ver, EVIDENCE_VERSION, Jsonb(report)))
    return report
