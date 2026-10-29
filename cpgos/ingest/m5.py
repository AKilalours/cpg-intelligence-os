"""Load the real M5 (Walmart) dataset: streaming, memory-bounded, provenance-preserving.

Analogy: instead of carrying every box off the truck at once (which is what
crashed on 59 million rows), boxes go down a conveyor belt. DuckDB is the
belt: it reads the wide file, reshapes it, checks every row, and spills to
disk when memory runs low, so peak memory stays near a fixed limit no matter
how big the file is.

Outputs from ONE validated pass:
- Daily rows -> Parquet files partitioned by store (for forecasting).
- Weekly totals (or daily, for small inputs) -> Postgres sales_observation
  (for the app), streamed in with COPY.
- Bad rows -> quarantined_row (capped, with the full count recorded).

Decisions worth defending:
- Days before an item's first listed price are "not on the shelf yet", not
  zero demand. They are dropped and counted (rows_skipped_prelaunch).
- M5 has no promotion flag; promo_flag stays NULL.
- Two source formats are accepted: the Kaggle release (calendar has a `d`
  column, sales has an `id` column) and a reformatted copy without those.
  For the copy, day labels are derived from date order ONLY after checking
  the dates are strictly consecutive; otherwise the load is refused.
- Validation is set-based SQL here (59M rows), and must agree with the
  row-level Python contract used for small files. A test enforces that.
- Re-running the same files is a no-op; a corrected release updates rows and
  moves their provenance to the new dataset_version.
"""

from __future__ import annotations

import csv
import re
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import duckdb
import psycopg
from psycopg.types.json import Jsonb

from cpgos.contracts import Column, Contract
from cpgos.datasets.registry import DATA_DIR, sha256_file
from cpgos.db import code_version

PARTNER_NAME = "Walmart (M5)"
PROCESSED_ROOT = DATA_DIR / "processed" / "m5"
QUARANTINE_STORE_CAP = 1000   # rows kept as evidence; the full count is always recorded
COPY_CHUNK_BYTES = 8 << 20

# The row-level contract. The SQL checks below implement the same rules for
# large files; tests/test_m5_ingest.py proves the two agree.
M5_SALES_CONTRACT = Contract(
    name="m5_sales_long",
    version="2.0.0",
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

_VIOLATIONS_SQL = r"""
list_filter([
    CASE WHEN units_raw IS NULL OR trim(units_raw) = '' THEN 'units: missing'
         WHEN NOT regexp_full_match(trim(units_raw), '-?[0-9]+') THEN 'units: not an integer'
         WHEN CAST(trim(units_raw) AS BIGINT) < 0 THEN 'units: below minimum 0' END,
    CASE WHEN sell_price_raw IS NULL OR trim(sell_price_raw) = '' THEN 'sell_price: missing'
         WHEN TRY_CAST(trim(sell_price_raw) AS DECIMAL(18,4)) IS NULL THEN 'sell_price: cannot parse decimal'
         WHEN CAST(trim(sell_price_raw) AS DECIMAL(18,4)) < 0.01 THEN 'sell_price: below minimum 0.01' END,
    CASE WHEN item_id IS NULL OR NOT regexp_full_match(item_id, '[A-Z_]+_[0-9]+_[0-9]{3}') THEN 'item_id: bad format' END,
    CASE WHEN dept_id IS NULL OR trim(dept_id) = '' THEN 'dept_id: missing' END,
    CASE WHEN cat_id IS NULL OR cat_id NOT IN ('HOBBIES', 'HOUSEHOLD', 'FOODS') THEN 'cat_id: not in allowed set' END,
    CASE WHEN store_id IS NULL OR NOT regexp_full_match(store_id, '[A-Z]{2}_[0-9]+') THEN 'store_id: bad format' END,
    CASE WHEN state_id IS NULL OR state_id NOT IN ('CA', 'TX', 'WI') THEN 'state_id: not in allowed set' END
], x -> x IS NOT NULL)
"""


class M5FormatError(ValueError):
    """Source files don't match either supported M5 layout."""


class M5IntegrityError(RuntimeError):
    """The pipeline itself lost or duplicated data. Always a bug, never a data problem."""


@dataclass
class LoadReport:
    run_id: int
    status: str
    rows_in: int
    rows_loaded: int
    rows_quarantined: int
    rows_skipped_prelaunch: int
    failure_reason: str | None = None
    source_format: str = ""
    pg_granularity: str = ""
    parquet_path: str | None = None
    parquet_rows: int = 0
    notes: list[str] = field(default_factory=list)


class _Rejected(Exception):
    pass


def _register_version(conn: psycopg.Connection, path: Path, origin: str, license_: str) -> int:
    return conn.execute(
        """INSERT INTO dataset_version (dataset_key, file_name, sha256, license, data_origin)
           VALUES ('m5', %s, %s, %s, %s)
           ON CONFLICT (dataset_key, sha256) DO UPDATE SET file_name = EXCLUDED.file_name
           RETURNING dataset_version_id""",
        (path.name, sha256_file(path), license_, origin),
    ).fetchone()[0]


def _q(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


ID_COLS = ("item_id", "dept_id", "cat_id", "store_id", "state_id")
_DAY_RE = re.compile(r"d_(\d+)")


def _melt_to_narrow(sales_p: Path, out_csv: Path, stores: list[str] | None, limit_series: int | None) -> None:
    """Reshape the wide sales file into narrow rows, one input line at a time.

    Memory stays at one line (about 2,000 values) regardless of file size.
    The file is read exactly once, in file order, so "the first N series" is
    well defined.
    """
    store_set = set(stores) if stores else None
    with sales_p.open(newline="") as fin, out_csv.open("w", newline="") as fout:
        reader = csv.reader(fin)
        header = next(reader, None)
        if header is None:
            raise M5FormatError("sales file is empty")
        missing = [c for c in ID_COLS if c not in header]
        if missing:
            raise M5FormatError(f"sales file is missing column {missing[0]!r}")
        id_idx = [header.index(c) for c in ID_COLS]
        store_idx = header.index("store_id")
        days = [(i, m.group(1)) for i, h in enumerate(header) if (m := _DAY_RE.fullmatch(h))]
        if not days:
            raise M5FormatError("sales file has no d_N day columns")
        writer = csv.writer(fout)
        writer.writerow([*ID_COLS, "di", "units_raw"])
        taken = 0
        for row in reader:
            if limit_series is not None and taken >= limit_series:
                break
            if store_set is not None and row[store_idx] not in store_set:
                continue
            ids = [row[i] if i < len(row) else "" for i in id_idx]
            writer.writerows([*ids, di, row[i] if i < len(row) else ""] for i, di in days)
            taken += 1


def _prepare(db: duckdb.DuckDBPyConnection, cal_p: Path, price_p: Path, sales_p: Path,
             limit_series: int | None, stores: list[str] | None, workdir: Path) -> str:
    """Build the validated `checked` table in DuckDB. Returns the detected source format."""
    cal_cols = [r[0] for r in db.execute(f"DESCRIBE SELECT * FROM read_csv({_q(cal_p)}, header=true, all_varchar=true)").fetchall()]
    for need in ("date", "wm_yr_wk"):
        if need not in cal_cols:
            raise M5FormatError(f"calendar.csv is missing column {need!r}")
    db.execute(f"CREATE TABLE cal_raw AS SELECT * FROM read_csv({_q(cal_p)}, header=true, all_varchar=true)")
    n, n_dates, span = db.execute(
        "SELECT count(*), count(DISTINCT CAST(date AS DATE)), max(CAST(date AS DATE)) - min(CAST(date AS DATE)) + 1 FROM cal_raw"
    ).fetchone()
    if "d" in cal_cols:
        fmt = "kaggle"
        db.execute("CREATE TABLE cal AS SELECT d, CAST(date AS DATE) AS obs_date, CAST(wm_yr_wk AS INTEGER) AS wk FROM cal_raw")
    else:
        # Deriving d_1..d_N from date order is only safe if dates are unique and consecutive.
        if not (n == n_dates == span):
            raise M5FormatError(
                f"calendar.csv has no 'd' column and its dates are not consecutive "
                f"({n} rows, {n_dates} distinct dates, {span}-day span); refusing to guess day labels"
            )
        fmt = "reformatted_copy"
        db.execute(
            """CREATE TABLE cal AS
               SELECT 'd_' || row_number() OVER (ORDER BY CAST(date AS DATE)) AS d,
                      CAST(date AS DATE) AS obs_date, CAST(wm_yr_wk AS INTEGER) AS wk
               FROM cal_raw"""
        )

    narrow = workdir / "long_raw.csv"
    _melt_to_narrow(sales_p, narrow, stores, limit_series)
    db.execute(
        f"""CREATE TABLE long_raw AS
            SELECT item_id, dept_id, cat_id, store_id, state_id, CAST(di AS INTEGER) AS di, units_raw
            FROM read_csv({_q(narrow)}, header=true, all_varchar=true)"""
    )
    # Memory design: every big join below uses small integer keys, and each
    # large intermediate is computed once into a disk-backed table.
    db.execute(
        """CREATE TABLE series AS
           SELECT row_number() OVER (ORDER BY store_id, item_id) AS sid,
                  item_id, dept_id, cat_id, store_id, state_id
           FROM (SELECT DISTINCT item_id, dept_id, cat_id, store_id, state_id FROM long_raw)"""
    )
    n_series, n_keys = db.execute(
        "SELECT count(*), count(DISTINCT (store_id, item_id)) FROM series").fetchone()
    if n_series != n_keys:
        raise M5FormatError(
            f"sales file lists some item-store pairs more than once ({n_series} rows, {n_keys} unique pairs); "
            "refusing to load because totals would be double-counted"
        )
    db.execute(
        f"""CREATE TABLE prices AS
            SELECT s.sid, CAST(p.wm_yr_wk AS INTEGER) AS wk, p.sell_price AS sell_price_raw
            FROM read_csv({_q(price_p)}, header=true, all_varchar=true) p
            JOIN series s ON s.store_id = p.store_id AND s.item_id = p.item_id"""
    )
    db.execute("CREATE TABLE first_wk AS SELECT sid, min(wk) AS first_wk FROM prices GROUP BY sid")
    db.execute("CREATE TABLE cal_i AS SELECT CAST(substr(d, 3) AS INTEGER) AS di, obs_date, wk FROM cal")
    db.execute(
        """CREATE TABLE long_t AS
           SELECT s.sid, l.di, l.units_raw
           FROM long_raw l
           JOIN series s ON s.store_id = l.store_id AND s.item_id = l.item_id"""
    )
    # Invariant: every series read must survive into long_t, with every day.
    n_days = db.execute("SELECT count(DISTINCT di) FROM long_raw").fetchone()[0]
    got_series, got_rows, raw_rows = db.execute(
        "SELECT (SELECT count(DISTINCT sid) FROM long_t), (SELECT count(*) FROM long_t), (SELECT count(*) FROM long_raw)"
    ).fetchone()
    if got_series != n_series or got_rows != raw_rows or raw_rows != n_series * n_days:
        raise M5IntegrityError(
            f"M5 reshape lost data: {got_series}/{n_series} series, {got_rows}/{raw_rows} rows, "
            f"expected {n_series} x {n_days} days"
        )
    # Pre-launch = no price yet for this item-store; never-priced items are entirely pre-launch.
    db.execute(
        f"""CREATE TABLE checked AS
            SELECT sid, item_id, dept_id, cat_id, store_id, state_id, d, units_raw,
                   obs_date, wk, sell_price_raw, {_VIOLATIONS_SQL} AS violations
            FROM (
              SELECT l.sid, s.item_id, s.dept_id, s.cat_id, s.store_id, s.state_id,
                     'd_' || l.di AS d, l.units_raw, c.obs_date, c.wk, p.sell_price_raw
              FROM long_t l
              JOIN cal_i c ON c.di = l.di
              JOIN first_wk f ON f.sid = l.sid
              JOIN series s ON s.sid = l.sid
              LEFT JOIN prices p ON p.sid = l.sid AND p.wk = c.wk
              WHERE c.wk >= f.first_wk
            )"""
    )
    return fmt


def load_m5(
    conn: psycopg.Connection,
    data_dir: Path,
    *,
    origin: str = "real",
    license_: str = "Kaggle competition rules",
    limit_series: int | None = None,
    stores: list[str] | None = None,
    batch: dict | None = None,
    pg_granularity: str = "week",
    parquet_root: Path | None = PROCESSED_ROOT,
    memory_limit: str = "1GB",
    threads: int = 2,
) -> LoadReport:
    if pg_granularity not in {"day", "week", "none"}:
        raise ValueError("pg_granularity must be 'day', 'week', or 'none'")
    if stores is not None and (not stores or not all(re.fullmatch(r"[A-Z]{2}_\d+", st) for st in stores)):
        raise ValueError(f"invalid store codes: {stores}")
    if (origin == "test_fixture" and parquet_root is not None
            and parquet_root.resolve().is_relative_to(PROCESSED_ROOT.resolve())):
        raise ValueError(
            "refusing to write test fixture output under the real processed-data folder "
            f"({PROCESSED_ROOT}); pass parquet_root=None or a temporary directory"
        )
    cal_p, price_p = data_dir / "calendar.csv", data_dir / "sell_prices.csv"
    sales_p = data_dir / "sales_train_evaluation.csv"

    with conn.transaction():
        ver = _register_version(conn, sales_p, origin, license_)
        _register_version(conn, cal_p, origin, license_)
        _register_version(conn, price_p, origin, license_)
        scope = {"partial": bool(stores or limit_series is not None)}
        if stores:
            scope["stores"] = sorted(stores)
        if limit_series is not None:
            scope["limit_series"] = int(limit_series)
        if batch:
            scope["batch"] = batch
        scope["pg_granularity"] = pg_granularity
        run_id = conn.execute(
            """INSERT INTO ingestion_run (dataset_version_id, contract_name, contract_version, code_version, status, scope)
               VALUES (%s, %s, %s, %s, 'running', %s) RETURNING run_id""",
            (ver, M5_SALES_CONTRACT.name, M5_SALES_CONTRACT.version, code_version(), Jsonb(scope)),
        ).fetchone()[0]

    try:
        with tempfile.TemporaryDirectory(prefix="cpgos_m5_") as tmp:
            tmpdir = Path(tmp)
            db = duckdb.connect(str(tmpdir / "work.duckdb"))
            # Ordinary (not TEMP) tables in this file-backed scratch database can spill
            # to disk; DuckDB TEMP tables are memory-resident and ran out of memory on real M5.
            db.execute(f"SET memory_limit = '{memory_limit}'")
            db.execute(f"SET threads = {int(threads)}")
            db.execute(f"SET temp_directory = {_q(tmpdir / 'spill')}")
            db.execute("SET preserve_insertion_order = false")
            try:
                fmt = _prepare(db, cal_p, price_p, sales_p, limit_series, stores, tmpdir)
            except M5FormatError as exc:
                _finish(conn, run_id, "rejected", 0, 0, 0, str(exc))
                db.close()
                return LoadReport(run_id, "rejected", 0, 0, 0, 0, str(exc))
            except M5IntegrityError as exc:
                _finish(conn, run_id, "failed", 0, 0, 0, str(exc))
                db.close()
                raise

            total_rows = db.execute("SELECT count(*) FROM long_t").fetchone()[0]
            rows_in, n_bad = db.execute("SELECT count(*), count(*) FILTER (WHERE len(violations) > 0) FROM checked").fetchone()
            skipped = total_rows - rows_in
            bad_sample = db.execute(
                """SELECT row_number() OVER (ORDER BY store_id, item_id, obs_date) AS rn,
                          item_id, dept_id, cat_id, store_id, state_id, d, CAST(obs_date AS VARCHAR) AS obs_date,
                          units_raw AS units, sell_price_raw AS sell_price, violations
                   FROM checked WHERE len(violations) > 0
                   ORDER BY store_id, item_id, obs_date LIMIT ?""",
                [QUARANTINE_STORE_CAP],
            ).fetchall()
            notes = [] if n_bad <= QUARANTINE_STORE_CAP else [
                f"{n_bad} rows failed checks; first {QUARANTINE_STORE_CAP} stored as evidence"]

            def store_quarantine() -> None:
                for rn, *vals, violations in bad_sample:
                    keys = ["item_id", "dept_id", "cat_id", "store_id", "state_id", "d", "obs_date", "units", "sell_price"]
                    conn.execute(
                        "INSERT INTO quarantined_row (run_id, row_number, raw, violations) VALUES (%s, %s, %s, %s)",
                        (run_id, rn, Jsonb(dict(zip(keys, vals))), Jsonb(list(violations))),
                    )

            rate_ok = rows_in > 0 and n_bad / rows_in <= M5_SALES_CONTRACT.max_quarantine_rate
            if not rate_ok:
                reason = (
                    "no rows after pre-launch filtering" if rows_in == 0 else
                    f"quarantine rate {n_bad / rows_in:.1%} exceeds {M5_SALES_CONTRACT.max_quarantine_rate:.1%}; "
                    "likely a source format change, batch rejected"
                )
                with conn.transaction():
                    store_quarantine()
                _finish(conn, run_id, "rejected", rows_in, 0, n_bad, reason)
                db.close()
                return LoadReport(run_id, "rejected", rows_in, 0, n_bad, skipped, reason, fmt, pg_granularity, notes=notes)

            db.execute(
                """CREATE TABLE clean AS
                   SELECT obs_date, wk, item_id, dept_id, cat_id, store_id, state_id,
                          CAST(trim(units_raw) AS BIGINT) AS units,
                          CAST(trim(sell_price_raw) AS DECIMAL(14,4)) AS sell_price
                   FROM checked WHERE len(violations) = 0"""
            )

            parquet_path, parquet_rows = None, 0
            if parquet_root is not None:
                # Origin is part of the path, so fixture or generated data can never
                # land in the folder that forecasting reads real data from.
                out = parquet_root / origin / sha256_file(sales_p)[:16]
                loaded_stores = [r[0] for r in db.execute("SELECT DISTINCT store_id FROM clean ORDER BY 1").fetchall()]
                todo = [st for st in loaded_stores if not (out / f"_SUCCESS_{st}").exists()]
                if todo:
                    out.mkdir(parents=True, exist_ok=True)
                    in_list = ", ".join(f"'{st}'" for st in todo)
                    db.execute(
                        f"""COPY (SELECT obs_date, item_id, dept_id, cat_id, store_id, state_id, units, sell_price
                                  FROM clean WHERE store_id IN ({in_list}) ORDER BY store_id, item_id, obs_date)
                            TO {_q(out)} (FORMAT parquet, PARTITION_BY (store_id), OVERWRITE_OR_IGNORE true)"""
                    )
                    for st in todo:
                        (out / f"_SUCCESS_{st}").write_text(f"dataset_version_id={ver}\n")
                parquet_path = str(out)
                parquet_rows = db.execute("SELECT count(*) FROM clean").fetchone()[0]

            stage_csv = tmpdir / "stage.csv"
            if pg_granularity == "day":
                db.execute(
                    f"""COPY (SELECT obs_date, item_id, dept_id, cat_id, store_id, state_id, units, sell_price FROM clean)
                        TO {_q(stage_csv)} (FORMAT csv, HEADER false)"""
                )
            elif pg_granularity == "week":
                # Week rows are dated by the first calendar day of the Walmart week,
                # so every series' weeks line up regardless of launch day.
                db.execute(
                    f"""COPY (
                          SELECT ws.week_start AS obs_date, item_id, dept_id, cat_id, store_id, state_id,
                                 sum(units) AS units, max(sell_price) AS sell_price
                          FROM clean JOIN (SELECT wk, min(obs_date) AS week_start FROM cal GROUP BY wk) ws USING (wk)
                          GROUP BY ALL)
                        TO {_q(stage_csv)} (FORMAT csv, HEADER false)"""
                )
            db.close()

            try:
                with conn.transaction():
                    store_quarantine()
                    loaded = 0 if pg_granularity == "none" else _write_postgres(conn, stage_csv, ver, pg_granularity)
                    _finish(conn, run_id, "succeeded", rows_in, loaded, n_bad, None, in_tx=True)
            except Exception as exc:
                _finish(conn, run_id, "failed", rows_in, 0, n_bad, f"{type(exc).__name__}: {exc}")
                raise

        return LoadReport(run_id, "succeeded", rows_in, loaded, n_bad, skipped, None, fmt, pg_granularity,
                          parquet_path, parquet_rows, notes)
    except BaseException as exc:
        # Ctrl+C, a crash, or a lost connection must never leave the run saying
        # "running" forever. Best effort here; if this connection is unusable,
        # the next load (holding the dataset lock) marks the run interrupted.
        _mark_failed_if_running(conn, run_id, exc)
        raise


def _mark_failed_if_running(conn: psycopg.Connection, run_id: int, exc: BaseException) -> None:
    reason = ("interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit))
              else f"{type(exc).__name__}: {str(exc)[:500]}")
    try:
        conn.execute(
            "UPDATE ingestion_run SET status='failed', failure_reason=%s, finished_at=now() "
            "WHERE run_id=%s AND status='running'",
            (reason, run_id),
        )
    except Exception:  # noqa: BLE001 - connection may be broken; stale-run cleanup covers it
        pass


def mark_stale_runs(conn: psycopg.Connection, dataset_key: str) -> int:
    """Mark runs left 'running' by a killed process as failed.

    Only safe while holding the dataset's load lock (see dataset_lock), because
    then no other load of this dataset can be in progress.
    """
    cur = conn.execute(
        """UPDATE ingestion_run r SET status='failed', finished_at=now(),
                  failure_reason='interrupted (process ended without finishing; found by a later load)'
           FROM dataset_version d
           WHERE r.dataset_version_id = d.dataset_version_id
             AND d.dataset_key = %s AND r.status = 'running'""",
        (dataset_key,),
    )
    return cur.rowcount


class LoadInProgressError(RuntimeError):
    pass


def dataset_lock(conn: psycopg.Connection, dataset_key: str):
    """Session-level advisory lock: one load per dataset at a time."""
    from contextlib import contextmanager

    @contextmanager
    def _lock():
        got = conn.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (f"cpgos:load:{dataset_key}",)).fetchone()[0]
        if not got:
            raise LoadInProgressError(f"another {dataset_key} load is already running against this database")
        try:
            yield
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (f"cpgos:load:{dataset_key}",))
    return _lock()


def discover_stores(sales_p: Path) -> list[str]:
    """Distinct store codes in file order, reading one line at a time."""
    seen: dict[str, None] = {}
    with sales_p.open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None or "store_id" not in header:
            raise M5FormatError("sales file is missing column 'store_id'")
        i = header.index("store_id")
        for row in reader:
            if i < len(row):
                seen.setdefault(row[i], None)
    return list(seen)


def _completed_stores(conn: psycopg.Connection, sales_sha: str, pg_granularity: str) -> set[str]:
    rows = conn.execute(
        """SELECT r.scope FROM ingestion_run r JOIN dataset_version d USING (dataset_version_id)
           WHERE d.dataset_key = 'm5' AND d.sha256 = %s AND r.status = 'succeeded'
             AND coalesce(r.scope->>'pg_granularity', 'week') = %s""",
        (sales_sha, pg_granularity),
    ).fetchall()
    done: set[str] = set()
    for (scope,) in rows:
        if not scope.get("partial"):
            return {"*"}  # a full single-pass load covers every store
        if "limit_series" not in scope:
            done.update(scope.get("stores", []))
    return done


def load_m5_by_store(
    conn: psycopg.Connection,
    data_dir: Path,
    *,
    origin: str = "real",
    license_: str = "Kaggle competition rules",
    stores: list[str] | None = None,
    pg_granularity: str = "week",
    parquet_root: Path | None = PROCESSED_ROOT,
    memory_limit: str = "1GB",
    threads: int = 2,
    force: bool = False,
    progress=print,
) -> list[LoadReport]:
    """Default way to load M5: one store at a time, resumable, one load at a time.

    Memory is bounded by the size of one store (verified: ~1.1 GB), not by the
    whole dataset. Stores that already loaded successfully from the same file
    are skipped, so re-running after a failure resumes where it stopped.
    """
    import uuid

    sales_p = data_dir / "sales_train_evaluation.csv"
    with dataset_lock(conn, "m5"):
        stale = mark_stale_runs(conn, "m5")
        if stale:
            progress(f"Marked {stale} earlier run(s) as interrupted (they never finished).")
        targets = stores or discover_stores(sales_p)
        done = set() if force else _completed_stores(conn, sha256_file(sales_p), pg_granularity)
        todo = [st for st in targets if "*" not in done and st not in done]
        if len(todo) < len(targets):
            progress(f"Skipping {len(targets) - len(todo)} store(s) already loaded from this exact file.")
        batch_id = uuid.uuid4().hex[:8]
        reports: list[LoadReport] = []
        for i, st in enumerate(todo, start=1):
            rep = load_m5(conn, data_dir, origin=origin, license_=license_, stores=[st],
                          batch={"id": batch_id, "part": i, "of": len(todo)},
                          pg_granularity=pg_granularity, parquet_root=parquet_root,
                          memory_limit=memory_limit, threads=threads)
            reports.append(rep)
            progress(f"[{i}/{len(todo)}] {st}: {rep.status}, {rep.rows_in:,} rows, "
                     f"{rep.rows_quarantined:,} quarantined")
            if rep.status != "succeeded":
                progress(f"Stopped at {st}: {rep.failure_reason}. Re-run to resume; finished stores are skipped.")
                break
        return reports


def _finish(conn, run_id, status, rows_in, loaded, quarantined, reason, in_tx=False) -> None:
    sql = """UPDATE ingestion_run SET status=%s, rows_in=%s, rows_loaded=%s, rows_quarantined=%s,
                 failure_reason=%s, finished_at=now() WHERE run_id=%s"""
    args = (status, rows_in, loaded, quarantined, reason, run_id)
    if in_tx:
        conn.execute(sql, args)
    else:
        with conn.transaction():
            conn.execute(sql, args)


def _write_postgres(conn: psycopg.Connection, stage_csv: Path, dataset_version_id: int, granularity: str) -> int:
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
    # Stream the staged file in fixed-size chunks: memory stays flat.
    with stage_csv.open("rb") as f, conn.cursor().copy("COPY stg_m5 FROM STDIN WITH (FORMAT csv)") as cp:
        while chunk := f.read(COPY_CHUNK_BYTES):
            cp.write(chunk)

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
           SELECT s.obs_date, %s, p.sku_id, st.store_id, s.units, s.sell_price, %s
           FROM stg_m5 s
           JOIN product p ON p.sku_code = s.item_id
           JOIN store st ON st.partner_id = %s AND st.external_code = s.store_id
           ON CONFLICT (obs_date, granularity, sku_id, store_id) DO UPDATE
               SET units = EXCLUDED.units,
                   shelf_price = EXCLUDED.shelf_price,
                   dataset_version_id = EXCLUDED.dataset_version_id
               WHERE (sales_observation.units, sales_observation.shelf_price)
                     IS DISTINCT FROM (EXCLUDED.units, EXCLUDED.shelf_price)""",
        (granularity, dataset_version_id, partner_id),
    )
    return cur.rowcount
