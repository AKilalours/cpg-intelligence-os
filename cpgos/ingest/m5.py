"""Load the real M5 (Walmart) dataset into the canonical model.

M5 ships "wide": one row per item-store, one column per day (d_1 ... d_1941).
We turn it "long" (one row per item-store-day), attach the weekly price,
validate every row against a contract, and load through a staging table.

Decisions worth defending in an interview:
- Days before an item's first listed price are dropped, not loaded as zeros.
  Those zeros mean "not on the shelf yet", not "nobody bought it", and they
  would teach a forecaster that demand was zero. They are counted in the run
  stats (rows_skipped_prelaunch) so nothing disappears silently.
- M5 has no promotion flag, so promo_flag stays NULL here. A derived
  price-drop flag is added later and labeled 'derived_price_drop'.
- Re-running the same file is a no-op; a corrected release updates rows and
  moves their provenance to the new dataset_version.
- rows_loaded counts rows actually inserted or changed, so a re-run reports 0.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from cpgos.contracts import Column, Contract, validate
from cpgos.datasets.registry import sha256_file
from cpgos.db import code_version

PARTNER_NAME = "Walmart (M5)"

M5_SALES_CONTRACT = Contract(
    name="m5_sales_long",
    version="1.0.0",
    columns=(
        Column("obs_date", "date"),
        Column("item_id", "str", pattern=r"[A-Z_]+_\d+_\d{3}"),
        Column("dept_id", "str"),
        Column("cat_id", "str", allowed=frozenset({"HOBBIES", "HOUSEHOLD", "FOODS"})),
        Column("store_id", "str", pattern=r"[A-Z]{2}_\d+"),
        Column("state_id", "str", allowed=frozenset({"CA", "TX", "WI"})),
        Column("units", "int", min_value=Decimal(0)),
        Column("sell_price", "decimal", min_value=Decimal("0.01")),
    ),
    max_quarantine_rate=0.001,
)


@dataclass
class LoadReport:
    run_id: int
    status: str
    rows_in: int
    rows_loaded: int
    rows_quarantined: int
    rows_skipped_prelaunch: int
    failure_reason: str | None = None


class _Rejected(Exception):
    pass


def _register_version(conn: psycopg.Connection, key: str, path: Path, origin: str, license_: str) -> int:
    digest = sha256_file(path)
    row = conn.execute(
        """INSERT INTO dataset_version (dataset_key, file_name, sha256, license, data_origin)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (dataset_key, sha256) DO UPDATE SET file_name = EXCLUDED.file_name
           RETURNING dataset_version_id""",
        (key, path.name, digest, license_, origin),
    ).fetchone()
    return row[0]


def _read_calendar(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    day_to_date, day_to_week = {}, {}
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            day_to_date[r["d"]] = r["date"]
            day_to_week[r["d"]] = r["wm_yr_wk"]
    return day_to_date, day_to_week


def _read_prices(path: Path) -> dict[tuple[str, str, str], str]:
    prices = {}
    with path.open(newline="") as f:
        for r in csv.DictReader(f):
            prices[(r["store_id"], r["item_id"], r["wm_yr_wk"])] = r["sell_price"]
    return prices


def _long_rows(sales_path: Path, day_to_date, day_to_week, prices, limit_series: int | None):
    """Yield long-format raw rows, then a final marker with the pre-launch skip count."""
    skipped = 0
    with sales_path.open(newline="") as f:
        reader = csv.DictReader(f)
        day_cols = [c for c in reader.fieldnames if c.startswith("d_")]
        for n, r in enumerate(reader):
            if limit_series is not None and n >= limit_series:
                break
            launched = False
            for d in day_cols:
                price = prices.get((r["store_id"], r["item_id"], day_to_week[d]))
                if price is None and not launched:
                    skipped += 1
                    continue
                launched = True
                yield {
                    "obs_date": day_to_date[d],
                    "item_id": r["item_id"],
                    "dept_id": r["dept_id"],
                    "cat_id": r["cat_id"],
                    "store_id": r["store_id"],
                    "state_id": r["state_id"],
                    "units": r[d],
                    "sell_price": price,  # None after launch = gap; the contract quarantines it
                }
    yield {"__skipped__": skipped}


def _quarantine(conn: psycopg.Connection, run_id: int, quarantined) -> None:
    for row_number, raw, violations in quarantined:
        conn.execute(
            "INSERT INTO quarantined_row (run_id, row_number, raw, violations) VALUES (%s, %s, %s, %s)",
            (run_id, row_number, Jsonb(raw), Jsonb(violations)),
        )


def load_m5(
    conn: psycopg.Connection,
    data_dir: Path,
    *,
    origin: str = "real",
    license_: str = "Kaggle competition rules",
    limit_series: int | None = None,
) -> LoadReport:
    cal_p, price_p = data_dir / "calendar.csv", data_dir / "sell_prices.csv"
    sales_p = data_dir / "sales_train_evaluation.csv"

    with conn.transaction():
        ver = _register_version(conn, "m5", sales_p, origin, license_)
        _register_version(conn, "m5", cal_p, origin, license_)
        _register_version(conn, "m5", price_p, origin, license_)
        run_id = conn.execute(
            """INSERT INTO ingestion_run (dataset_version_id, contract_name, contract_version, code_version, status)
               VALUES (%s, %s, %s, %s, 'running') RETURNING run_id""",
            (ver, M5_SALES_CONTRACT.name, M5_SALES_CONTRACT.version, code_version()),
        ).fetchone()[0]

    day_to_date, day_to_week = _read_calendar(cal_p)
    prices = _read_prices(price_p)
    raw_rows, skipped = [], 0
    for row in _long_rows(sales_p, day_to_date, day_to_week, prices, limit_series):
        if "__skipped__" in row:
            skipped = row["__skipped__"]
        else:
            raw_rows.append(row)

    header = [c.name for c in M5_SALES_CONTRACT.columns]
    result = validate(M5_SALES_CONTRACT, header, raw_rows)

    try:
        with conn.transaction():
            _quarantine(conn, run_id, result.quarantined)
            if not result.accepted:
                raise _Rejected("; ".join(result.batch_errors))
            loaded = _write_clean(conn, result.clean, ver)
            conn.execute(
                """UPDATE ingestion_run SET status='succeeded', rows_in=%s, rows_loaded=%s,
                       rows_quarantined=%s, finished_at=now() WHERE run_id=%s""",
                (result.rows_in, loaded, len(result.quarantined), run_id),
            )
        return LoadReport(run_id, "succeeded", result.rows_in, loaded, len(result.quarantined), skipped)
    except _Rejected as exc:
        # Data writes rolled back; record the rejection and its quarantine evidence separately.
        with conn.transaction():
            _quarantine(conn, run_id, result.quarantined)
            conn.execute(
                """UPDATE ingestion_run SET status='rejected', rows_in=%s, rows_loaded=0,
                       rows_quarantined=%s, failure_reason=%s, finished_at=now() WHERE run_id=%s""",
                (result.rows_in, len(result.quarantined), str(exc), run_id),
            )
        return LoadReport(run_id, "rejected", result.rows_in, 0, len(result.quarantined), skipped, str(exc))


def _write_clean(conn: psycopg.Connection, rows: list[dict], dataset_version_id: int) -> int:
    partner_id = conn.execute(
        """INSERT INTO trading_partner (name, partner_type) VALUES (%s, 'retailer')
           ON CONFLICT (name, partner_type) DO UPDATE SET name = EXCLUDED.name
           RETURNING partner_id""",
        (PARTNER_NAME,),
    ).fetchone()[0]

    conn.execute(
        """CREATE TEMP TABLE stg_m5 (
               obs_date DATE, item_id TEXT, dept_id TEXT, cat_id TEXT,
               store_id TEXT, state_id TEXT, units NUMERIC, sell_price NUMERIC
           ) ON COMMIT DROP"""
    )
    buf = io.StringIO()
    w = csv.writer(buf)
    for r in rows:
        w.writerow([r["obs_date"], r["item_id"], r["dept_id"], r["cat_id"],
                    r["store_id"], r["state_id"], r["units"], r["sell_price"]])
    with conn.cursor().copy("COPY stg_m5 FROM STDIN WITH (FORMAT csv)") as cp:
        cp.write(buf.getvalue())

    conn.execute(
        """INSERT INTO store (partner_id, external_code, region)
           SELECT DISTINCT %s, store_id, state_id FROM stg_m5
           ON CONFLICT (partner_id, external_code) DO NOTHING""",
        (partner_id,),
    )
    conn.execute(
        """INSERT INTO product (sku_code, category, department)
           SELECT DISTINCT item_id, cat_id, dept_id FROM stg_m5
           ON CONFLICT (sku_code) DO NOTHING"""
    )
    conn.execute(
        """INSERT INTO product_identifier (sku_id, id_type, id_value, source_system, resolution_method, confidence)
           SELECT DISTINCT p.sku_id, 'source_item', s.item_id, 'm5', 'exact', 1
           FROM stg_m5 s JOIN product p ON p.sku_code = s.item_id
           ON CONFLICT (id_type, id_value, source_system) DO NOTHING"""
    )
    cur = conn.execute(
        """INSERT INTO sales_observation
               (obs_date, granularity, sku_id, store_id, units, shelf_price, dataset_version_id)
           SELECT s.obs_date, 'day', p.sku_id, st.store_id, s.units, s.sell_price, %s
           FROM stg_m5 s
           JOIN product p ON p.sku_code = s.item_id
           JOIN store st ON st.partner_id = %s AND st.external_code = s.store_id
           ON CONFLICT (obs_date, granularity, sku_id, store_id) DO UPDATE
               SET units = EXCLUDED.units,
                   shelf_price = EXCLUDED.shelf_price,
                   dataset_version_id = EXCLUDED.dataset_version_id
               WHERE (sales_observation.units, sales_observation.shelf_price)
                     IS DISTINCT FROM (EXCLUDED.units, EXCLUDED.shelf_price)""",
        (dataset_version_id, partner_id),
    )
    return cur.rowcount
