"""Generate a real-anchored financial world (see docs/phase2a_reconciliation.md).

Analogy: a flight simulator built from real weather data. The weather (what
stores sold, at what price, which weeks had price drops) is real M5 data. The
flights (invoices, payments, remittances, deductions) are generated on top of
it, following published KeHE rules, with realistic mistakes injected at known
rates. Every injected mistake is recorded in a hidden answer key (eval.*), so
the matching and validation engines can be scored honestly.

Deterministic: the same inputs, config, and seed produce the same world.
"""

from __future__ import annotations

import hashlib
import json
import random
import statistics
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg

from cpgos.money import Money, to_decimal

GENERATOR_VERSION = "1.0.0"
BRAND = "Northfield Pantry Co."      # fictional; owns anonymized M5 FOODS items
DISTRIBUTOR = "KeHE"
RETAILER = "Walmart (M5)"
CENTS = Decimal("0.01")

# Published KeHE terms (sources in the registry and docs); everything else is an assumption.
PUBLISHED_TERMS = {
    "late_shipment_fee": (Decimal("250"), "https://www.spscommerce.com/community/articles/common-kehe-deductions-and-how-to-minimize-them"),
    "dispute_window_days": (Decimal("180"), "https://endlesscommerce.com/playbook/kehe-and-unfi-deductions-codes-and-clocks/"),
}


@dataclass(frozen=True)
class WorldConfig:
    seed: int = 7
    weeks: int = 104
    n_skus: int = 60
    wholesale_share: str = "0.62"
    case_pack: int = 12
    payment_terms_days: int = 30
    promo_allowance_share: str = "0.60"
    promo_drop_threshold: str = "0.90"     # price <= 90% of trailing 8-week max
    spoils_allowance_pct: str = "0.010"
    # Remittance noise on invoice payment lines
    p_typo_ref: float = 0.06
    p_missing_ref: float = 0.03
    p_wrong_ref: float = 0.02
    p_partial: float = 0.05
    p_unpaid: float = 0.01
    # Shipments and receipts
    p_late: float = 0.04
    p_shortage_invoice: float = 0.08       # share of deliveries with one line short by one case
    # Deduction validity mix
    p_promo_overclaim: float = 0.10
    p_promo_duplicate: float = 0.04
    p_promo_unsupported: float = 0.03      # share of promotions that also get a claim for a non-promo week
    p_false_shortage_invoice: float = 0.03 # share of deliveries hit with a shortage claim despite full receipt
    p_false_late_fee: float = 0.01

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class WeeklySale:
    """One real M5 row aggregated to an ISO week."""
    store: str
    region: str
    item: str
    category: str
    department: str
    week: date          # Monday
    units: int
    price: Decimal      # weekly shelf price
    days: int           # days present in the week (7 = full week)


@dataclass
class WorldReport:
    dataset_version_id: int
    as_of: date
    skus: int
    stores: int
    invoices: int = 0
    invoice_lines: int = 0
    promotions: int = 0
    payments: int = 0
    remittance_lines: int = 0
    deductions: int = 0
    deductions_invalid: int = 0
    deductions_generated: int = 0
    deductions_pending: int = 0      # claimed but not yet taken on a check by as_of (still in the backlog)
    noise_counts: dict = field(default_factory=dict)


def _cents(x: Decimal) -> Decimal:
    return Money(x).amount


def weekly_sales_from_parquet(parquet_dir: Path, weeks: int, n_skus: int, seed: int, memory_limit: str = "1GB") -> list[WeeklySale]:
    """Read real M5 daily rows from Parquet and aggregate to ISO weeks for a seeded set of FOODS items.

    Only items sold at every store in every week of the window (full 7-day
    weeks) are eligible, so the brand's catalogue is stable over the period.
    """
    import duckdb

    db = duckdb.connect()
    db.execute(f"SET memory_limit = '{memory_limit}'")
    src = f"read_parquet('{str(parquet_dir).rstrip('/')}/**/*.parquet', hive_partitioning=true)"
    last = db.execute(f"SELECT max(obs_date) FROM {src}").fetchone()[0]
    # Window ends on the last complete Sunday.
    end = last - timedelta(days=(last.weekday() + 1) % 7)
    start = end - timedelta(days=7 * weeks - 1)
    db.execute(f"""
        CREATE TABLE w AS
        SELECT store_id, state_id, item_id, cat_id, dept_id,
               date_trunc('week', obs_date)::DATE AS wk,
               sum(units)::BIGINT AS units, max(sell_price) AS price, count(*) AS days
        FROM {src}
        WHERE cat_id = 'FOODS' AND obs_date BETWEEN DATE '{start}' AND DATE '{end}'
        GROUP BY ALL""")
    eligible = [r[0] for r in db.execute(f"""
        SELECT item_id FROM w GROUP BY item_id
        HAVING count(*) = {weeks} * (SELECT count(DISTINCT store_id) FROM w) AND min(days) = 7
        ORDER BY item_id""").fetchall()]
    rng = random.Random(seed)
    chosen = sorted(rng.sample(eligible, min(n_skus, len(eligible))))
    rows = db.execute(
        "SELECT store_id, state_id, item_id, cat_id, dept_id, wk, units, price, days FROM w "
        "WHERE item_id IN (SELECT unnest(?)) ORDER BY wk, store_id, item_id", [chosen]).fetchall()
    return [WeeklySale(r[0], r[1], r[2], r[3], r[4], r[5], int(r[6]), Decimal(str(r[7])), int(r[8])) for r in rows]


def _upsert(conn, sql, params):
    return conn.execute(sql, params).fetchone()[0]


def _corrupt_ref(rng: random.Random, ref: str) -> str:
    """A realistic keying error: swap two adjacent digits, or drop one."""
    digits = [i for i, ch in enumerate(ref) if ch.isdigit()]
    if len(digits) >= 2 and rng.random() < 0.6:
        i = rng.choice(digits[:-1])
        if ref[i] != ref[i + 1]:
            return ref[:i] + ref[i + 1] + ref[i] + ref[i + 2:]
    i = rng.choice(digits)
    return ref[:i] + ref[i + 1:]


def build_world(conn: psycopg.Connection, sales: list[WeeklySale], cfg: WorldConfig = WorldConfig(),
                source_note: str = "M5") -> WorldReport:
    """Write the world into the ledger and its answer key into eval.*. One world per database."""
    if not sales:
        raise ValueError("no sales rows to build a world from")
    existing = conn.execute("SELECT count(*) FROM dataset_version WHERE dataset_key = 'finance_world'").fetchone()[0]
    if existing:
        raise RuntimeError("a financial world already exists in this database; use a fresh database to regenerate")

    rng = random.Random(cfg.seed)
    weeks = sorted({s.week for s in sales})
    as_of = weeks[-1] + timedelta(days=6)
    fingerprint = hashlib.sha256(
        (cfg.digest() + GENERATOR_VERSION + source_note +
         hashlib.sha256(Path(__file__).read_bytes()).hexdigest() +
         json.dumps([(s.store, s.item, str(s.week), s.units, str(s.price)) for s in sales[:2000]])).encode()
    ).hexdigest()
    rep: WorldReport

    with conn.transaction():
        ver = _upsert(conn, """INSERT INTO dataset_version (dataset_key, file_name, sha256, license, data_origin)
                               VALUES ('finance_world', %s, %s, %s, 'real_anchored_generated') RETURNING dataset_version_id""",
                      (f"generator {GENERATOR_VERSION}, seed {cfg.seed}, {cfg.weeks} weeks, source {source_note}",
                       fingerprint, "Generated on real M5 sales and published KeHE terms; see docs/phase2a_reconciliation.md"))
        conn.execute("INSERT INTO world_clock (dataset_version_id, as_of_date) VALUES (%s, %s)", (ver, as_of))

        brand = _upsert(conn, """INSERT INTO brand (name) VALUES (%s) ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                                 RETURNING brand_id""", (BRAND,))
        kehe = _upsert(conn, """INSERT INTO trading_partner (name, partner_type) VALUES (%s, 'distributor')
                                ON CONFLICT (name, partner_type) DO UPDATE SET name = EXCLUDED.name RETURNING partner_id""", (DISTRIBUTOR,))
        retailer = _upsert(conn, """INSERT INTO trading_partner (name, partner_type) VALUES (%s, 'retailer')
                                    ON CONFLICT (name, partner_type) DO UPDATE SET name = EXCLUDED.name RETURNING partner_id""", (RETAILER,))
        for key, (value, src) in PUBLISHED_TERMS.items():
            conn.execute("INSERT INTO trade_term VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING", (kehe, key, value, src))
        for key, value in (("payment_terms_days", cfg.payment_terms_days), ("spoils_allowance_pct", cfg.spoils_allowance_pct),
                           ("promo_allowance_share", cfg.promo_allowance_share)):
            conn.execute("INSERT INTO trade_term VALUES (%s, %s, %s, 'assumption') ON CONFLICT DO NOTHING", (kehe, key, value))

        stores = {}
        for s in sorted({(x.store, x.region) for x in sales}):
            stores[s[0]] = _upsert(conn, """INSERT INTO store (partner_id, external_code, region) VALUES (%s, %s, %s)
                                            ON CONFLICT (partner_id, external_code) DO UPDATE SET region = EXCLUDED.region
                                            RETURNING store_id""", (retailer, s[0], s[1]))
        skus = {}
        for it in sorted({(x.item, x.category, x.department) for x in sales}):
            skus[it[0]] = _upsert(conn, """INSERT INTO product (sku_code, brand_id, category, department) VALUES (%s, %s, %s, %s)
                                           ON CONFLICT (sku_code) DO UPDATE SET brand_id = EXCLUDED.brand_id
                                           RETURNING sku_id""", (it[0], brand, it[1], it[2]))
        codes = dict(conn.execute("SELECT code, deduction_code_id FROM deduction_code WHERE partner_id = %s", (kehe,)).fetchall())
        missing = {"WP", "WG", "IA"} - set(codes)
        if missing:
            raise RuntimeError(f"KeHE deduction codes {sorted(missing)} not loaded; run: python scripts/load_data.py kehe_deduction_codes")

        rep = WorldReport(ver, as_of, len(skus), len(stores))
        by_key = {(s.store, s.item, s.week): s for s in sales}

        # Retailer POS units per store, SKU, ISO week: the real M5 units, which are
        # what a scan-back claim must be checked against.
        with conn.cursor().copy("COPY retailer_pos_week (store_id, sku_id, week_start, units, dataset_version_id) FROM STDIN") as cp:
            for s in sales:
                cp.write_row((stores[s.store], skus[s.item], s.week, s.units, ver))

        # Wholesale price per SKU per quarter: share of the median real shelf price.
        share = Decimal(cfg.wholesale_share)
        q_prices: dict[tuple[str, tuple[int, int]], list[Decimal]] = {}
        for s in sales:
            q_prices.setdefault((s.item, (s.week.year, (s.week.month - 1) // 3)), []).append(s.price)
        wholesale = {k: _cents(statistics.median(v) * share) for k, v in q_prices.items()}

        def unit_cost(item: str, wk: date) -> Decimal:
            return wholesale[(item, (wk.year, (wk.month - 1) // 3))]

        # Promotions: real price drops, merged over consecutive weeks.
        promos = []          # (store, item, start_week, end_week, allowance)
        drop = Decimal(cfg.promo_drop_threshold)
        ashare = Decimal(cfg.promo_allowance_share)
        for store in stores:
            for item in skus:
                hist = [by_key[(store, item, w)] for w in weeks if (store, item, w) in by_key]
                cur = None
                for i, s in enumerate(hist):
                    prior = [h.price for h in hist[max(0, i - 8):i]]
                    reg = max(prior) if len(prior) >= 4 else None
                    on = reg is not None and s.price <= drop * reg and s.days == 7
                    if on:
                        allowance = _cents(ashare * (reg - s.price))
                        if allowance <= 0:
                            on = False
                    if on and cur and cur[3] == s.week - timedelta(days=7) and cur[4] == allowance:
                        cur[3] = s.week
                    elif on:
                        if cur:
                            promos.append(tuple(cur))
                        cur = [store, item, s.week, s.week, allowance]
                    elif cur:
                        promos.append(tuple(cur)); cur = None
                if cur:
                    promos.append(tuple(cur))
        promo_ids = {}
        for store, item, w0, w1, allowance in promos:
            pid = _upsert(conn, """INSERT INTO promotion (brand_id, partner_id, promo_type, start_date, end_date,
                                       allowance_per_unit, store_id, dataset_version_id)
                                   VALUES (%s, %s, 'scan_back', %s, %s, %s, %s, %s) RETURNING promotion_id""",
                          (brand, kehe, w0, w1 + timedelta(days=6), allowance, stores[store], ver))
            conn.execute("INSERT INTO promotion_sku VALUES (%s, %s)", (pid, skus[item]))
            promo_ids[(store, item, w0)] = pid
        rep.promotions = len(promos)

        # Invoices, shipments, receipts.
        invoices = []        # dicts used later for payments and deductions
        seq = 1_048_000
        for wk in weeks:
            for store in stores:
                lines = []
                for item in skus:
                    s = by_key.get((store, item, wk))
                    if not s or s.units <= 0:
                        continue
                    qty = -(-s.units // cfg.case_pack) * cfg.case_pack
                    cost = unit_cost(item, wk)
                    lines.append((item, qty, cost, Money.of(cost).times(qty)))
                if not lines:
                    continue
                seq += rng.randint(1, 9)
                number = f"NP{seq:07d}"
                total = Money.of(0)
                for *_, amt in lines:
                    total = total + amt
                inv_date = wk
                inv = _upsert(conn, """INSERT INTO invoice (invoice_number, brand_id, partner_id, store_id, invoice_date, due_date,
                                           total_amount, dataset_version_id)
                                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING invoice_id""",
                              (number, brand, kehe, stores[store], inv_date, inv_date + timedelta(days=cfg.payment_terms_days),
                               total.amount, ver))
                scheduled = inv_date + timedelta(days=2)
                late = rng.random() < cfg.p_late
                delivered = scheduled + timedelta(days=rng.randint(1, 4)) if late else scheduled
                conn.execute("INSERT INTO shipment (invoice_id, scheduled_date, delivered_date, dataset_version_id) VALUES (%s, %s, %s, %s)",
                             (inv, scheduled, delivered, ver))
                line_rows = []
                # Shortages happen to deliveries, not to every line: pick at most one short line per invoice.
                short_line = rng.randrange(len(lines)) + 1 if rng.random() < cfg.p_shortage_invoice else None
                for n, (item, qty, cost, amt) in enumerate(lines, start=1):
                    lid = _upsert(conn, """INSERT INTO invoice_line (invoice_id, line_number, sku_id, quantity, unit_price, line_amount)
                                           VALUES (%s, %s, %s, %s, %s, %s) RETURNING invoice_line_id""",
                                  (inv, n, skus[item], qty, cost, amt.amount))
                    short = n == short_line and qty >= cfg.case_pack
                    received = qty - cfg.case_pack if short else qty
                    conn.execute("INSERT INTO receipt_line VALUES (%s, %s)", (lid, received))
                    line_rows.append({"id": lid, "item": item, "qty": qty, "received": received, "cost": cost})
                pay_on = inv_date + timedelta(days=cfg.payment_terms_days + rng.randint(-5, 5))  # drawn once
                invoices.append({"id": inv, "number": number, "store": store, "date": inv_date, "total": total,
                                 "late": late, "delivered": delivered, "lines": line_rows, "pay_on": pay_on})
                rep.invoices += 1
                rep.invoice_lines += len(lines)

        # Deductions, each with its hidden truth.
        window = int(PUBLISHED_TERMS["dispute_window_days"][0])
        deductions = []      # dicts: code, amount, date, invoice, truth...

        def add_ded(code, amount: Money, ddate, *, invoice=None, store=None, sku=None, units=None, p0=None, p1=None,
                    promotion=None, reason_text, valid: bool, valid_amount: Money, reason):
            if amount.amount <= 0 or ddate > as_of:
                return
            store = store or (invoice["store"] if invoice else None)
            deductions.append(dict(code=code, amount=amount, date=ddate, invoice=invoice, store=store, sku=sku, units=units, p0=p0, p1=p1,
                                   promotion=promotion, reason_text=reason_text, valid=valid,
                                   valid_amount=valid_amount, reason=reason))

        promo_by_start = {(p[0], p[1], p[2]): p for p in promos}
        promo_weeks: dict[tuple[str, str], set] = {}
        for p in promos:
            wk_ = p[2]
            while wk_ <= p[3]:
                promo_weeks.setdefault((p[0], p[1]), set()).add(wk_)
                wk_ += timedelta(days=7)
        for (store, item, w0), pid in promo_ids.items():
            _, _, _, w1, allowance = promo_by_start[(store, item, w0)]
            sold = sum(by_key[(store, item, w)].units for w in weeks if w0 <= w <= w1)
            correct = Money.of(allowance).times(sold)
            ddate = w1 + timedelta(days=6 + rng.randint(14, 45))
            kw = dict(store=store, sku=skus[item], p0=w0, p1=w1 + timedelta(days=6), promotion=pid, reason_text="WALMART PROMO SCANBACK")
            if rng.random() < cfg.p_promo_overclaim:
                claimed = int(sold * rng.uniform(1.10, 1.40)) + 1
                add_ded("WP", Money.of(allowance).times(claimed), ddate, units=claimed, valid=False,
                        valid_amount=correct, reason="overclaim", **kw)
            else:
                add_ded("WP", correct, ddate, units=sold, valid=True, valid_amount=correct, reason="valid", **kw)
                if rng.random() < cfg.p_promo_duplicate:
                    add_ded("WP", correct, ddate + timedelta(days=rng.randint(7, 40)), units=sold, valid=False,
                            valid_amount=Money.of(0), reason="duplicate", **kw)
            if rng.random() < cfg.p_promo_unsupported:
                # A claim for a week with no price drop at all for this store and item.
                calm = [w for w in weeks if w not in promo_weeks.get((store, item), set())]
                if calm:
                    w = rng.choice(calm)
                    units = by_key[(store, item, w)].units
                    add_ded("WP", Money.of(allowance).times(units), w + timedelta(days=6 + rng.randint(14, 45)),
                            store=store, sku=skus[item], units=units, p0=w, p1=w + timedelta(days=6), promotion=None,
                            reason_text="WALMART PROMO SCANBACK", valid=False, valid_amount=Money.of(0), reason="no_agreement")

        late_fee = Money.of(PUBLISHED_TERMS["late_shipment_fee"][0])
        spoils_pct = Decimal(cfg.spoils_allowance_pct)
        quarter_value: dict[tuple[str, tuple[int, int]], Money] = {}
        for inv in invoices:
            full = [ln for ln in inv["lines"] if ln["received"] == ln["qty"]]
            false_claim = rng.choice(full) if full and rng.random() < cfg.p_false_shortage_invoice else None
            for ln in inv["lines"]:
                if ln["received"] < ln["qty"]:
                    short_units = ln["qty"] - ln["received"]
                    amt = Money.of(ln["cost"]).times(short_units)
                    add_ded("IA", amt, inv["delivered"] + timedelta(days=rng.randint(5, 20)), invoice=inv, sku=skus[ln["item"]],
                            units=short_units, reason_text="SHORTAGE", valid=True, valid_amount=amt, reason="valid")
                elif ln is false_claim:
                    amt = Money.of(ln["cost"]).times(cfg.case_pack)
                    add_ded("IA", amt, inv["delivered"] + timedelta(days=rng.randint(5, 20)), invoice=inv, sku=skus[ln["item"]],
                            units=cfg.case_pack, reason_text="SHORTAGE", valid=False, valid_amount=Money.of(0),
                            reason="no_shortage_on_receipt")
            if inv["late"]:
                add_ded("IA", late_fee, inv["delivered"] + timedelta(days=rng.randint(20, 60)), invoice=inv,
                        reason_text="LATE DELIVERY FEE", valid=True, valid_amount=late_fee, reason="valid")
            elif rng.random() < cfg.p_false_late_fee:
                add_ded("IA", late_fee, inv["delivered"] + timedelta(days=rng.randint(20, 60)), invoice=inv,
                        reason_text="LATE DELIVERY FEE", valid=False, valid_amount=Money.of(0), reason="delivered_on_time")
            q = (inv["store"], (inv["date"].year, (inv["date"].month - 1) // 3))
            quarter_value[q] = quarter_value.get(q, Money.of(0)) + inv["total"]
        for (store, (yr, qi)), value in sorted(quarter_value.items()):
            q_end = date(yr + (qi == 3), 1 if qi == 3 else 3 * qi + 4, 1) - timedelta(days=1)
            q_start = date(yr, 3 * qi + 1, 1)
            pct = Decimal(str(round(rng.uniform(0.005, 0.016), 4)))
            claim = value.times(pct)
            allowed = value.times(spoils_pct)
            ok = claim.amount <= allowed.amount
            add_ded("WG", claim, q_end + timedelta(days=30), store=store, p0=q_start, p1=q_end, reason_text="WALMART SPOILS",
                    valid=ok, valid_amount=claim if ok else allowed, reason="valid" if ok else "over_allowance")

        # Payments: one check per Friday, paying invoices due that week; deductions taken on the next check.
        ded_rows = sorted(deductions, key=lambda d: d["date"])
        paid_invoices = set()
        pay_seq = 600_000
        rem_count = 0
        noise_counts = {"clean": 0, "typo_ref": 0, "missing_ref": 0, "wrong_ref": 0, "partial": 0}
        friday = weeks[0] + timedelta(days=4)
        di = 0
        backlog: list[dict] = []     # deductions claimed but not yet taken on a check
        while friday <= as_of:
            due = [inv for inv in invoices if inv["id"] not in paid_invoices and inv["pay_on"] <= friday]
            while di < len(ded_rows) and ded_rows[di]["date"] <= friday:
                backlog.append(ded_rows[di]); di += 1
            lines = []
            for inv in due:
                paid_invoices.add(inv["id"])
                if rng.random() < cfg.p_unpaid:
                    continue
                amount, noise, ref = inv["total"], "clean", inv["number"]
                r = rng.random()
                if r < cfg.p_partial:
                    amount, noise = inv["total"].times(Decimal(str(round(rng.uniform(0.5, 0.9), 2)))), "partial"
                elif r < cfg.p_partial + cfg.p_typo_ref:
                    ref, noise = _corrupt_ref(rng, ref), "typo_ref"
                elif r < cfg.p_partial + cfg.p_typo_ref + cfg.p_missing_ref:
                    ref, noise = None, "missing_ref"
                elif r < cfg.p_partial + cfg.p_typo_ref + cfg.p_missing_ref + cfg.p_wrong_ref:
                    others = [o for o in invoices if o["id"] != inv["id"] and o["store"] != inv["store"]
                              and abs((o["date"] - inv["date"]).days) <= 28]
                    if others:
                        ref, noise = rng.choice(others)["number"], "wrong_ref"
                noise_counts[noise] += 1
                lines.append(("pay", inv, ref, amount, noise))
            # Take deductions oldest first while the check stays positive; the rest wait for a
            # later check. (Carrying the whole week's batch all-or-nothing let the backlog
            # snowball until no check could absorb it, and 3 of 4 deductions were never written.)
            total = Money.of(0)
            for ln in lines:
                total = total + ln[3]
            waiting = []
            for d in backlog:
                if d["amount"].amount < total.amount:
                    lines.append(("ded", d))
                    total = total - d["amount"]
                else:
                    waiting.append(d)
            backlog = waiting
            if not lines:
                friday += timedelta(days=7)
                continue
            pay_seq += rng.randint(1, 50)
            pid = _upsert(conn, """INSERT INTO payment (partner_id, payment_reference, payment_date, amount, dataset_version_id)
                                   VALUES (%s, %s, %s, %s, %s) RETURNING payment_id""",
                          (kehe, f"ACH{pay_seq:08d}", friday, total.amount, ver))
            rep.payments += 1
            for n, ln in enumerate(lines, start=1):
                if ln[0] == "pay":
                    _, inv, ref, amount, noise = ln
                    rl = _upsert(conn, """INSERT INTO remittance_line (payment_id, line_number, raw_invoice_ref, gross_amount,
                                              deduction_amount, net_amount)
                                          VALUES (%s, %s, %s, %s, 0, %s) RETURNING remittance_line_id""",
                                 (pid, n, ref, amount.amount, amount.amount))
                    conn.execute("INSERT INTO eval.gt_remittance_alloc VALUES (%s, %s, %s, %s)", (rl, inv["id"], amount.amount, noise))
                else:
                    d = ln[1]
                    rl = _upsert(conn, """INSERT INTO remittance_line (payment_id, line_number, raw_invoice_ref, raw_code,
                                              gross_amount, deduction_amount, net_amount)
                                          VALUES (%s, %s, %s, %s, NULL, %s, %s) RETURNING remittance_line_id""",
                                 (pid, n, d["invoice"]["number"] if d["invoice"] else None, d["code"],
                                  d["amount"].amount, (-d["amount"]).amount))
                    pay_seq += 1
                    did = _upsert(conn, """INSERT INTO deduction (claim_number, partner_id, remittance_line_id, deduction_code_id,
                                               raw_reason, amount, deduction_date, dispute_deadline, raw_invoice_ref, sku_id,
                                               claimed_units, period_start, period_end, store_id, dataset_version_id)
                                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING deduction_id""",
                                  (f"R-{rl:07d}", kehe, rl, codes[d["code"]], d["reason_text"], d["amount"].amount, d["date"],
                                   d["date"] + timedelta(days=window), d["invoice"]["number"] if d["invoice"] else None,
                                   d["sku"], d["units"], d["p0"], d["p1"], stores[d["store"]] if d["store"] else None, ver))
                    conn.execute("INSERT INTO eval.gt_deduction VALUES (%s, %s, %s, %s)",
                                 (did, d["valid"], d["valid_amount"].amount, d["reason"]))
                    rep.deductions += 1
                    rep.deductions_invalid += 0 if d["valid"] else 1
                rem_count += 1
            friday += timedelta(days=7)
        rep.deductions_generated = len(ded_rows)
        rep.deductions_pending = len(backlog) + (len(ded_rows) - di)
        rep.remittance_lines = rem_count
        rep.noise_counts = noise_counts
    return rep
