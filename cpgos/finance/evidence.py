"""Realistic evidence for deduction checks: gaps, restatements, and typos.

Why this exists: the financial world creates claims from true events and
records the evidence (retailer POS, receipts, shipments, the promotion
calendar) perfectly. A checker reading perfect evidence produced by the same
rules can only score 100%, which proves the arithmetic and nothing else.
Real files are messier: the retailer's sales feed misses a week, a receipt is
never scanned, a delivery date is not keyed in, the promotion calendar has a
promotion missing or ends a week off, and a claim arrives without its store.

Version 2.0 (this file) fixes two flaws of 1.0:

1. 1.0 altered the system of record: it deleted receipts and promotions and
   blanked dates and stores. Now the damage is an overlay (schema "evidence")
   and the checker reads schema "observed", the records with the overlay
   applied. Analogy: the checker gets a photocopy with pages missing; the
   original stays complete in the cabinet. The engine role cannot read the
   originals or the overlay.
2. 1.0 drew random numbers in database-id order, so a machine whose ids or
   rows differed damaged different records with the same seed. Now whether a
   record is damaged is a hash of the seed and the record's business key
   (store code, item code, week, invoice number, claim number). Ids and
   processing order cannot change the result, and one differing record can
   only change its own fate, never every record after it.

Every change is logged in eval.gt_evidence (with business keys), and the
report carries fingerprints of the damaged sets and of the world itself, so
two machines can prove they ran on the same data with the same damage.

`restore_v1` repairs a database that 1.0 altered: it rebuilds the same world
in a temporary database, copies the original values back by business key,
and refuses to finish unless every repaired table then equals the rebuild.
"""

from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Callable

import psycopg
from psycopg.types.json import Jsonb

EVIDENCE_VERSION = "evidence-2.0.0"
V1 = "evidence-1.0.0"
OVERLAY_TABLES = ("pos_week", "receipt_missing", "delivery_missing", "promo_missing", "promo_end", "claim_store_missing")


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


def draw(seed: int, kind: str, key: str) -> float:
    """A uniform number in [0, 1) fixed by the seed, the kind of damage, and the record's business key."""
    h = hashlib.sha256(f"{seed}|{kind}|{key}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


def _fingerprint(lines) -> str:
    return hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()[:16]


def _world_version(conn) -> int:
    ver = conn.execute("""SELECT dataset_version_id FROM dataset_version WHERE dataset_key = 'finance_world'
                          ORDER BY dataset_version_id DESC LIMIT 1""").fetchone()
    if ver is None:
        raise RuntimeError("no financial world in this database; run: make finance-world")
    return ver[0]


# ---------- business keys (identical on every machine that built the same world) ----------

def _pos(conn, ver):
    return conn.execute(
        """SELECT p.store_id, p.sku_id, p.week_start, p.units, tp.name || '|' || s.external_code || '|' || pr.sku_code
                  || '|' || p.week_start
           FROM retailer_pos_week p JOIN store s USING (store_id) JOIN trading_partner tp ON tp.partner_id = s.partner_id
           JOIN product pr USING (sku_id) WHERE p.dataset_version_id = %s""", (ver,)).fetchall()


def _receipts(conn, ver):
    return conn.execute(
        """SELECT r.invoice_line_id, r.units_received, tp.name || '|' || i.invoice_number || '|' || il.line_number
           FROM receipt_line r JOIN invoice_line il USING (invoice_line_id) JOIN invoice i USING (invoice_id)
           JOIN trading_partner tp ON tp.partner_id = i.partner_id WHERE i.dataset_version_id = %s""", (ver,)).fetchall()


def _shipments(conn, ver):
    return conn.execute(
        """SELECT s.shipment_id, s.scheduled_date, s.delivered_date, tp.name || '|' || i.invoice_number
           FROM shipment s JOIN invoice i USING (invoice_id) JOIN trading_partner tp ON tp.partner_id = i.partner_id
           WHERE s.dataset_version_id = %s""", (ver,)).fetchall()


_PROMO_KEY = """tp.name || '|' || b.name || '|' || coalesce(sp.name || '/' || s.external_code, '') || '|' || p.promo_type || '|' ||
                p.start_date || '|' || coalesce(p.allowance_per_unit::text, '') || '|' ||
                coalesce(p.discount_per_unit::text, '') || '|' || coalesce(p.discount_pct::text, '') || '|' ||
                coalesce(p.planned_spend::text, '') || '|' ||
                coalesce((SELECT string_agg(pr.sku_code, ',' ORDER BY pr.sku_code) FROM promotion_sku ps
                          JOIN product pr USING (sku_id) WHERE ps.promotion_id = p.promotion_id), '')"""


def _promotions(conn, ver):
    """(promotion_id, start, end, key without the end date). The end date is left out of the key
    because it is one of the things the calendar can get wrong."""
    return conn.execute(
        f"""SELECT p.promotion_id, p.start_date, p.end_date, {_PROMO_KEY}
            FROM promotion p JOIN trading_partner tp ON tp.partner_id = p.partner_id JOIN brand b USING (brand_id)
            LEFT JOIN store s ON s.store_id = p.store_id LEFT JOIN trading_partner sp ON sp.partner_id = s.partner_id
            WHERE p.dataset_version_id = %s""", (ver,)).fetchall()


def _claims(conn, ver):
    return conn.execute(
        """SELECT d.deduction_id, sp.name || '/' || s.external_code, tp.name || '|' || d.claim_number
           FROM deduction d JOIN trading_partner tp USING (partner_id) LEFT JOIN store s ON s.store_id = d.store_id
           LEFT JOIN trading_partner sp ON sp.partner_id = s.partner_id
           WHERE d.dataset_version_id = %s""", (ver,)).fetchall()


def world_fingerprint(conn, ver: int | None = None) -> str:
    """A hash of the records no damage version touches (invoices, lines, claims). Equal on two machines
    means both built the same world."""
    ver = ver or _world_version(conn)
    lines = [r[0] for r in conn.execute(
        """SELECT 'inv|' || tp.name || '|' || i.invoice_number || '|' || i.invoice_date || '|' || i.total_amount
           FROM invoice i JOIN trading_partner tp USING (partner_id) WHERE i.dataset_version_id = %s
           UNION ALL
           SELECT 'line|' || tp.name || '|' || i.invoice_number || '|' || il.line_number || '|' || il.quantity || '|' ||
                  il.unit_price FROM invoice_line il JOIN invoice i USING (invoice_id)
           JOIN trading_partner tp USING (partner_id) WHERE i.dataset_version_id = %s
           UNION ALL
           SELECT 'claim|' || tp.name || '|' || d.claim_number || '|' || d.amount || '|' || d.deduction_date
           FROM deduction d JOIN trading_partner tp USING (partner_id) WHERE d.dataset_version_id = %s""",
        (ver, ver, ver))]
    return _fingerprint(lines)


# ---------- version 2.0: overlay, decided by business key ----------

def degrade(conn: psycopg.Connection, cfg: EvidenceConfig = EvidenceConfig(), replace: bool = False) -> dict:
    """Write the evidence overlay for this database's financial world, once. The records are not changed."""
    ver = _world_version(conn)
    v1 = conn.execute("SELECT report FROM finance_evidence WHERE dataset_version_id = %s AND version = %s",
                      (ver, V1)).fetchone()
    if v1 and "restored" not in v1[0]:
        raise RuntimeError("evidence-1.0.0 altered this world's records; repair them first: make finance-restore-evidence")
    done = conn.execute("SELECT report FROM finance_evidence WHERE dataset_version_id = %s AND version = %s",
                        (ver, EVIDENCE_VERSION)).fetchone()
    if done and not replace:
        return {**done[0], "already_applied": True}
    s = cfg.seed
    log: list[tuple[str, str, dict]] = []
    counts = {}
    with conn.transaction():
        for t in OVERLAY_TABLES:                                  # one overlay per database (one world per database)
            conn.execute(f"DELETE FROM evidence.{t}")
        conn.execute("DELETE FROM eval.gt_evidence WHERE dataset_version_id = %s AND version = %s", (ver, EVIDENCE_VERSION))
        conn.execute("DELETE FROM finance_evidence WHERE dataset_version_id = %s AND version = %s", (ver, EVIDENCE_VERSION))

        pos = _pos(conn, ver)
        n_missing = n_restated = 0
        for st, sk, wk, u, key in pos:
            if draw(s, "pos_week_missing", key) < cfg.p_pos_week_missing:
                conn.execute("INSERT INTO evidence.pos_week VALUES (%s, %s, %s, NULL)", (st, sk, wk))
                log.append(("pos_week_missing", key, {"units": u}))
                n_missing += 1
            elif u > 0 and draw(s, "pos_week_restated", key) < cfg.p_pos_week_restated:
                sign = -1 if draw(s, "pos_week_restated_sign", key) < 0.5 else 1
                pct = 0.01 + 0.02 * draw(s, "pos_week_restated_size", key)
                new = max(0, round(u * (1 + sign * pct)))
                if new == u:
                    new = u + 1
                conn.execute("INSERT INTO evidence.pos_week VALUES (%s, %s, %s, %s)", (st, sk, wk, new))
                log.append(("pos_week_restated", key, {"true_units": u, "reported_units": new}))
                n_restated += 1
        counts["pos_weeks"] = {"total": len(pos), "missing": n_missing, "restated": n_restated}

        rec = _receipts(conn, ver)
        lost = [(lid, key) for lid, _, key in rec if draw(s, "receipt_missing", key) < cfg.p_receipt_missing]
        for lid, key in lost:
            conn.execute("INSERT INTO evidence.receipt_missing VALUES (%s)", (lid,))
            log.append(("receipt_missing", key, {}))
        counts["receipt_lines"] = {"total": len(rec), "missing": len(lost)}

        ship = [r for r in _shipments(conn, ver) if r[2] is not None]
        undated = [(sid, d, key) for sid, _, d, key in ship
                   if draw(s, "delivery_date_missing", key) < cfg.p_delivery_date_missing]
        for sid, d, key in undated:
            conn.execute("INSERT INTO evidence.delivery_missing VALUES (%s)", (sid,))
            log.append(("delivery_date_missing", key, {"true_delivered": str(d)}))
        counts["shipments"] = {"total": len(ship), "undated": len(undated)}

        promos = _promotions(conn, ver)
        n_dropped = n_shifted = 0
        for pid, start, end, key in promos:
            key = f"{key}|{end}"                                   # the true end identifies the agreement here
            if draw(s, "promo_missing", key) < cfg.p_promo_missing:
                conn.execute("INSERT INTO evidence.promo_missing VALUES (%s)", (pid,))
                log.append(("promo_missing", key, {"start": str(start), "end": str(end)}))
                n_dropped += 1
            elif draw(s, "promo_end_shifted", key) < cfg.p_promo_end_shifted:
                new_end = end + timedelta(days=-7 if draw(s, "promo_end_shifted_sign", key) < 0.5 else 7)
                if new_end < start:
                    new_end = end + timedelta(days=7)
                conn.execute("INSERT INTO evidence.promo_end VALUES (%s, %s)", (pid, new_end))
                log.append(("promo_end_shifted", key, {"true_end": str(end), "keyed_end": str(new_end)}))
                n_shifted += 1
        counts["promotions"] = {"total": len(promos), "missing": n_dropped, "end_shifted": n_shifted}

        claims = [r for r in _claims(conn, ver) if r[1] is not None]
        storeless = [(did, code, key) for did, code, key in claims
                     if draw(s, "claim_store_missing", key) < cfg.p_claim_store_missing]
        for did, code, key in storeless:
            conn.execute("INSERT INTO evidence.claim_store_missing VALUES (%s)", (did,))
            log.append(("claim_store_missing", key, {"store": code}))
        counts["claims"] = {"with_store": len(claims), "store_removed": len(storeless)}

        with conn.cursor().copy("COPY eval.gt_evidence (dataset_version_id, kind, ref, detail, version) FROM STDIN") as cp:
            for kind, ref, detail in log:
                cp.write_row((ver, kind, ref, Jsonb(detail), EVIDENCE_VERSION))
        by_kind = defaultdict(list)
        for kind, ref, _ in log:
            by_kind[kind].append(ref)
        report = {"version": EVIDENCE_VERSION, "config": asdict(cfg), "changes": len(log), **counts,
                  "world_fingerprint": world_fingerprint(conn, ver),
                  "damage_fingerprint": _fingerprint(f"{k}|{r}" for k, r, _ in log),
                  "fingerprint_by_kind": {k: _fingerprint(v) for k, v in sorted(by_kind.items())}}
        conn.execute("INSERT INTO finance_evidence (dataset_version_id, version, report) VALUES (%s, %s, %s)",
                     (ver, EVIDENCE_VERSION, Jsonb(report)))
    return report


# ---------- repair of databases that version 1.0 altered ----------

class WorldMismatch(RuntimeError):
    pass


def snapshot(conn: psycopg.Connection, ver: int | None = None) -> dict:
    """Every record version 1.0 could have changed, by business key, plus the untouched world's fingerprint."""
    ver = ver or _world_version(conn)
    promos = defaultdict(list)
    for _, _, end, key in _promotions(conn, ver):
        promos[key].append(str(end))
    return {"world": world_fingerprint(conn, ver),
            "pos": {k: u for *_, u, k in _pos(conn, ver)},
            "receipts": {k: str(u) for _, u, k in _receipts(conn, ver)},
            "shipments": {k: (str(a), str(b)) for _, a, b, k in _shipments(conn, ver)},
            "promotions": {k: sorted(v) for k, v in promos.items()},
            "claims": {k: code for _, code, k in _claims(conn, ver)}}


def _url_on_same_server(url: str, dbname: str) -> str:
    base, _, query = url.partition("?")
    return base.rsplit("/", 1)[0] + f"/{dbname}" + (f"?{query}" if query else "")


def restore_v1(conn: psycopg.Connection, db_url: str, rebuild: Callable[[psycopg.Connection], None],
               progress=print) -> dict:
    """Undo evidence-1.0.0's changes to the records. `rebuild(conn)` must build the same world into an
    empty migrated database; its records are the reference. Nothing is guessed: the repair is refused
    unless the rebuilt world has the same fingerprint, and committed only if every repaired table then
    equals the rebuild exactly."""
    from cpgos.db import migrate
    ver = _world_version(conn)
    row = conn.execute("SELECT report FROM finance_evidence WHERE dataset_version_id = %s AND version = %s",
                       (ver, V1)).fetchone()
    if row is None or "restored" in row[0]:
        return {"nothing_to_restore": True}
    tmp = f"cpgos_restore_{uuid.uuid4().hex[:8]}"
    admin = _url_on_same_server(db_url, "postgres")
    with psycopg.connect(admin, autocommit=True) as a:
        a.execute(f'CREATE DATABASE "{tmp}"')
    try:
        progress(f"rebuilding the world in a temporary database ({tmp})")
        with psycopg.connect(_url_on_same_server(db_url, tmp), autocommit=True) as t:
            migrate(t)
            rebuild(t)
            ref = snapshot(t)
        with conn.transaction():                                   # all or nothing
            renumbered = renumber_claims(conn, ver)
            live = snapshot(conn, ver)
            if live["world"] != ref["world"]:
                raise WorldMismatch(f"the rebuilt world ({ref['world']}) is not this database's world ({live['world']}); "
                                    "it was made by another generator. Build a fresh world instead of repairing.")
            progress("same world confirmed; copying original values back by business key")
            fixed = _copy_back(conn, ver, live, ref)
            if renumbered:
                fixed["claims_renumbered"] = renumbered
            after = snapshot(conn, ver)
            differ = [k for k in ref if after[k] != ref[k]]
            if differ:
                raise RuntimeError(f"repair incomplete for {differ}; nothing was committed")
            logged = {k: v for k, v in conn.execute(
                "SELECT kind, count(*) FROM eval.gt_evidence WHERE dataset_version_id = %s AND version = %s GROUP BY kind",
                (ver, V1))}
            result = {"repaired": fixed, "logged_by_1_0": logged, "world_fingerprint": ref["world"],
                      "repair_equals_log": all(fixed.get(k, 0) == v for k, v in logged.items())}
            conn.execute("UPDATE finance_evidence SET report = report || %s WHERE dataset_version_id = %s AND version = %s",
                         (Jsonb({"restored": result}), ver, V1))
    finally:
        with psycopg.connect(admin, autocommit=True) as a:
            a.execute(f'DROP DATABASE IF EXISTS "{tmp}" WITH (FORCE)')
    return result


def renumber_claims(conn, ver: int) -> int:
    """Give claims the numbers generator 1.1 gives them (the remittance line's position in the world).
    Generator 1.0 used database ids, which differ between databases. Returns how many changed."""
    canon = conn.execute(
        """SELECT d.deduction_id, 'R-' || lpad(o.n::text, 7, '0') FROM deduction d JOIN (
               SELECT rl.remittance_line_id, row_number() OVER (ORDER BY rl.remittance_line_id) AS n
               FROM remittance_line rl JOIN payment p USING (payment_id) WHERE p.dataset_version_id = %s) o
           USING (remittance_line_id) WHERE d.dataset_version_id = %s AND d.claim_number <> 'R-' || lpad(o.n::text, 7, '0')""",
        (ver, ver)).fetchall()
    for did, _ in canon:                                          # two steps: numbers may swap places
        conn.execute("UPDATE deduction SET claim_number = 'renumbering-' || deduction_id WHERE deduction_id = %s", (did,))
    for did, num in canon:
        conn.execute("UPDATE deduction SET claim_number = %s WHERE deduction_id = %s", (num, did))
    return len(canon)


def _copy_back(conn, ver, live, ref) -> dict:
    n = defaultdict(int)
    store = {f"{p}/{c}": i for i, p, c in conn.execute(
        "SELECT s.store_id, tp.name, s.external_code FROM store s JOIN trading_partner tp USING (partner_id)")}
    sku = dict((c, i) for i, c in conn.execute("SELECT sku_id, sku_code FROM product"))

    for key, units in ref["pos"].items():
        if live["pos"].get(key) == units:
            continue
        p, st, sk, wk = key.split("|")
        if key in live["pos"]:
            conn.execute("UPDATE retailer_pos_week SET units = %s WHERE store_id = %s AND sku_id = %s AND week_start = %s",
                         (units, store[f"{p}/{st}"], sku[sk], wk))
            n["pos_week_restated"] += 1
        else:
            conn.execute("INSERT INTO retailer_pos_week (store_id, sku_id, week_start, units, dataset_version_id) "
                         "VALUES (%s, %s, %s, %s, %s)", (store[f"{p}/{st}"], sku[sk], wk, units, ver))
            n["pos_week_missing"] += 1

    lines = {f"{p}|{inv}|{ln}": lid for lid, p, inv, ln in conn.execute(
        """SELECT il.invoice_line_id, tp.name, i.invoice_number, il.line_number FROM invoice_line il
           JOIN invoice i USING (invoice_id) JOIN trading_partner tp USING (partner_id) WHERE i.dataset_version_id = %s""",
        (ver,))}
    for key, units in ref["receipts"].items():
        if key not in live["receipts"]:
            conn.execute("INSERT INTO receipt_line VALUES (%s, %s)", (lines[key], units))
            n["receipt_missing"] += 1

    for key, (_, delivered) in ref["shipments"].items():
        if live["shipments"].get(key, (None, None))[1] != delivered:
            p, inv = key.split("|", 1)
            conn.execute("""UPDATE shipment s SET delivered_date = %s FROM invoice i JOIN trading_partner tp USING (partner_id)
                            WHERE s.invoice_id = i.invoice_id AND tp.name = %s AND i.invoice_number = %s
                              AND i.dataset_version_id = %s""", (delivered, p, inv, ver))
            n["delivery_date_missing"] += 1

    live_ids = defaultdict(list)
    for pid, _, end, key in _promotions(conn, ver):
        live_ids[key].append((str(end), pid))
    for key, ends in ref["promotions"].items():
        have = sorted(live_ids.get(key, []))
        want = list(ends)
        unmatched_live = []
        for e, pid in have:                                       # promotions whose end is already right
            if e in want:
                want.remove(e)
            else:
                unmatched_live.append(pid)
        for pid, e in zip(unmatched_live, sorted(want)):          # ends keyed a week off
            conn.execute("UPDATE promotion SET end_date = %s WHERE promotion_id = %s", (e, pid))
            n["promo_end_shifted"] += 1
        for e in sorted(want)[len(unmatched_live):]:              # promotions 1.0 deleted
            _insert_promotion(conn, ver, key, e, store, sku)
            n["promo_missing"] += 1

    for key, code in ref["claims"].items():
        if live["claims"].get(key) != code and code is not None:
            p, claim = key.split("|", 1)
            conn.execute("""UPDATE deduction d SET store_id = %s FROM trading_partner tp
                            WHERE tp.partner_id = d.partner_id AND tp.name = %s AND d.claim_number = %s
                              AND d.dataset_version_id = %s""", (store[code], p, claim, ver))
            n["claim_store_missing"] += 1
    return dict(n)


def _insert_promotion(conn, ver, key, end, store, sku):
    p, brand, st, ptype, start, allow, disc, pct, spend, skus = key.split("|")
    partner = conn.execute("SELECT partner_id FROM trading_partner WHERE name = %s", (p,)).fetchone()[0]
    brand_id = conn.execute("SELECT brand_id FROM brand WHERE name = %s", (brand,)).fetchone()[0]
    pid = conn.execute(
        """INSERT INTO promotion (brand_id, partner_id, promo_type, start_date, end_date, discount_per_unit, discount_pct,
               planned_spend, dataset_version_id, store_id, allowance_per_unit)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING promotion_id""",
        (brand_id, partner, ptype, start, end, disc or None, pct or None, spend or None, ver,
         store[st] if st else None, allow or None)).fetchone()[0]
    for code in filter(None, skus.split(",")):
        conn.execute("INSERT INTO promotion_sku VALUES (%s, %s)", (pid, sku[code]))
