import csv
import shutil
from decimal import Decimal

import duckdb
import pytest

import cpgos.ingest.m5 as m5mod
from cpgos.contracts import validate
from cpgos.ingest.m5 import M5_SALES_CONTRACT, load_m5
from tests.conftest import FIXTURES

M5 = FIXTURES / "m5"


def _load(conn, path=M5, **kw):
    kw.setdefault("pg_granularity", "day")
    kw.setdefault("parquet_root", None)
    return load_m5(conn, path, origin="test_fixture", license_="fixture", **kw)


def _copy_fixture(tmp_path):
    for f in M5.glob("*.csv"):
        shutil.copy(f, tmp_path / f.name)
    return tmp_path


def _edit_sales(path, edits):
    p = path / "sales_train_evaluation.csv"
    rows = list(csv.reader(p.open()))
    for (r, c), v in edits.items():
        rows[r][c] = v
    with p.open("w", newline="") as f:
        csv.writer(f).writerows(rows)


def test_load_counts_and_prelaunch_skip(conn):
    rep = _load(conn)
    assert rep.status == "succeeded" and rep.source_format == "kaggle"
    # HOBBIES item: 10 days priced. FOODS item: first 7 days had no price (not launched).
    assert rep.rows_skipped_prelaunch == 7
    assert rep.rows_in == 13 and rep.rows_loaded == 13 and rep.rows_quarantined == 0
    assert conn.execute("SELECT count(*) FROM sales_observation").fetchone()[0] == 13


def test_every_row_traces_to_a_dataset_version(conn):
    _load(conn)
    orphan = conn.execute(
        """SELECT count(*) FROM sales_observation s
           LEFT JOIN dataset_version d USING (dataset_version_id)
           WHERE d.sha256 IS NULL"""
    ).fetchone()[0]
    assert orphan == 0


def test_rerun_is_a_noop(conn):
    _load(conn)
    rep2 = _load(conn)
    assert rep2.status == "succeeded" and rep2.rows_loaded == 0
    assert conn.execute("SELECT count(*) FROM sales_observation").fetchone()[0] == 13


def test_corrected_release_updates_rows_and_provenance(conn, tmp_path):
    _load(conn)
    d = _copy_fixture(tmp_path)
    _edit_sales(d, {(1, -1): "9"})  # correct one day's units for HOBBIES_1_001
    rep = _load(conn, d)
    assert rep.rows_loaded == 1
    units = conn.execute(
        """SELECT s.units FROM sales_observation s JOIN product p USING (sku_id)
           WHERE p.sku_code='HOBBIES_1_001' AND s.obs_date='2011-02-07'"""
    ).fetchone()[0]
    assert units == 9
    versions = conn.execute(
        "SELECT count(DISTINCT sha256) FROM dataset_version WHERE file_name='sales_train_evaluation.csv'"
    ).fetchone()[0]
    assert versions == 2


def test_corrupt_batch_is_rejected_and_nothing_loads(conn, tmp_path):
    d = _copy_fixture(tmp_path)
    _edit_sales(d, {(1, 8): "-3"})  # negative units: impossible
    rep = _load(conn, d)
    assert rep.status == "rejected" and rep.rows_quarantined == 1
    assert conn.execute("SELECT count(*) FROM sales_observation").fetchone()[0] == 0
    status, reason = conn.execute("SELECT status, failure_reason FROM ingestion_run").fetchone()
    assert status == "rejected" and "quarantine rate" in reason
    violations = conn.execute("SELECT violations FROM quarantined_row").fetchone()[0]
    assert "units" in violations[0]


def _python_long_rows(d):
    """Independent, deliberately simple melt used only to cross-check the SQL path."""
    cal = {r["d"]: r for r in csv.DictReader((d / "calendar.csv").open())}
    prices = {(r["store_id"], r["item_id"], r["wm_yr_wk"]): r["sell_price"]
              for r in csv.DictReader((d / "sell_prices.csv").open())}
    out = []
    for r in csv.DictReader((d / "sales_train_evaluation.csv").open()):
        first = min((int(w) for (s, i, w) in prices if s == r["store_id"] and i == r["item_id"]), default=None)
        for day in [k for k in r if k.startswith("d_")]:
            wk = cal[day]["wm_yr_wk"]
            if first is None or int(wk) < first:
                continue
            out.append({"obs_date": cal[day]["date"], "item_id": r["item_id"], "dept_id": r["dept_id"],
                        "cat_id": r["cat_id"], "store_id": r["store_id"], "state_id": r["state_id"],
                        "units": r[day], "sell_price": prices.get((r["store_id"], r["item_id"], wk))})
    return out


def test_sql_checks_agree_with_python_contract(conn, tmp_path):
    d = _copy_fixture(tmp_path)
    # Four kinds of damage: negative units, non-integer units, bad category (whole row), missing price.
    _edit_sales(d, {(1, 7): "-1", (1, 9): "1.5", (2, 3): "SNACKS"})
    prices = (d / "sell_prices.csv").read_text().splitlines()
    (d / "sell_prices.csv").write_text("\n".join(p for p in prices if "HOBBIES_1_001,11102" not in p) + "\n")

    expected = {}
    for row in validate(M5_SALES_CONTRACT, [c.name for c in M5_SALES_CONTRACT.columns], _python_long_rows(d)).quarantined:
        raw, errs = row[1], row[2]
        expected[(raw["item_id"], raw["obs_date"])] = sorted(e.split(":")[0] for e in errs)

    rep = _load(conn, d)
    assert rep.status == "rejected"
    got = {(r["item_id"], r["obs_date"]): sorted(v.split(":")[0] for v in viol)
           for r, viol in conn.execute("SELECT raw, violations FROM quarantined_row").fetchall()}
    assert got == expected and len(got) >= 6


def test_reformatted_copy_loads_identically(conn, tmp_path):
    d = _copy_fixture(tmp_path)
    cal = list(csv.DictReader((d / "calendar.csv").open()))
    with (d / "calendar.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[k for k in cal[0] if k != "d"], extrasaction="ignore")
        w.writeheader(); w.writerows(cal)
    rows = list(csv.reader((d / "sales_train_evaluation.csv").open()))
    with (d / "sales_train_evaluation.csv").open("w", newline="") as f:
        csv.writer(f).writerows([r[1:] for r in rows])  # drop the id column
    rep = _load(conn, d)
    assert rep.status == "succeeded" and rep.source_format == "reformatted_copy"
    assert (rep.rows_in, rep.rows_skipped_prelaunch) == (13, 7)


def test_gapped_calendar_without_day_labels_is_refused(conn, tmp_path):
    d = _copy_fixture(tmp_path)
    cal = list(csv.DictReader((d / "calendar.csv").open()))
    del cal[4]  # a missing date makes derived d_N labels wrong
    with (d / "calendar.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[k for k in cal[0] if k != "d"], extrasaction="ignore")
        w.writeheader(); w.writerows(cal)
    rep = _load(conn, d)
    assert rep.status == "rejected" and "not consecutive" in rep.failure_reason


def test_weekly_totals(conn):
    rep = _load(conn, pg_granularity="week")
    assert rep.status == "succeeded" and rep.rows_loaded == 3
    rows = dict(
        ((sku, str(d)), units) for sku, d, units in conn.execute(
            """SELECT p.sku_code, s.obs_date, s.units FROM sales_observation s JOIN product p USING (sku_id)
               WHERE s.granularity='week'""")
    )
    assert rows == {("HOBBIES_1_001", "2011-01-29"): Decimal(3), ("HOBBIES_1_001", "2011-02-05"): Decimal(4),
                    ("FOODS_3_090", "2011-02-05"): Decimal(15)}


def test_parquet_output_is_complete_and_written_once(conn, tmp_path):
    rep = _load(conn, parquet_root=tmp_path / "pq", pg_granularity="none")
    assert rep.parquet_rows == 13 and rep.rows_loaded == 0
    out = tmp_path / "pq"
    files = list(out.rglob("*.parquet"))
    assert files and all("store_id=CA_1" in str(f) and "/test_fixture/" in str(f) for f in files)
    (n, units), = duckdb.sql(f"SELECT count(*), sum(units) FROM read_parquet('{out}/**/*.parquet')").fetchall()
    assert (n, units) == (13, 22)
    mtimes = {f: f.stat().st_mtime_ns for f in files}
    _load(conn, parquet_root=tmp_path / "pq", pg_granularity="none")
    assert {f: f.stat().st_mtime_ns for f in files} == mtimes  # second run did not rewrite


def test_quarantine_storage_is_capped_but_count_is_exact(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(m5mod, "QUARANTINE_STORE_CAP", 1)
    d = _copy_fixture(tmp_path)
    _edit_sales(d, {(1, 7): "-1", (1, 8): "-2"})
    rep = _load(conn, d)
    assert rep.rows_quarantined == 2
    assert conn.execute("SELECT count(*) FROM quarantined_row").fetchone()[0] == 1
    assert rep.notes and "first 1 stored" in rep.notes[0]


def test_bad_granularity_rejected(conn):
    with pytest.raises(ValueError):
        _load(conn, pg_granularity="month")


def test_fixture_output_can_never_reach_the_real_data_folder(conn):
    # Regression: an API test once wrote fixture Parquet into data/processed/m5.
    with pytest.raises(ValueError, match="refusing to write test fixture output"):
        _load(conn, parquet_root=m5mod.PROCESSED_ROOT / "anything")


def test_duplicate_series_is_refused(conn, tmp_path):
    d = _copy_fixture(tmp_path)
    rows = list(csv.reader((d / "sales_train_evaluation.csv").open()))
    dup = list(rows[1]); dup[3] = "HOUSEHOLD"  # same item-store, conflicting category
    with (d / "sales_train_evaluation.csv").open("w", newline="") as f:
        csv.writer(f).writerows(rows + [dup])
    rep = _load(conn, d)
    assert rep.status == "rejected" and "more than once" in rep.failure_reason


def test_subset_load_takes_the_first_n_series_completely(conn):
    # Regression: a subset load once dropped about a third of its series because the
    # sales file was read twice and the two reads returned different rows.
    rep = _load(conn, limit_series=1)
    assert rep.status == "succeeded"
    assert rep.rows_in == 10  # HOBBIES_1_001: all 10 priced days, nothing dropped
    assert conn.execute("SELECT count(DISTINCT sku_id) FROM sales_observation").fetchone()[0] == 1


def test_scope_marks_partial_and_full_loads(conn):
    _load(conn, stores=["CA_1"])
    _load(conn)
    scopes = [r[0] for r in conn.execute("SELECT scope FROM ingestion_run ORDER BY run_id")]
    assert scopes == [{"partial": True, "stores": ["CA_1"], "pg_granularity": "day"},
                      {"partial": False, "pg_granularity": "day"}]


def test_store_filter_rejects_unknown_codes_and_empty_slices(conn):
    with pytest.raises(ValueError):
        _load(conn, stores=["CA_1'); DROP TABLE x; --"])
    assert conn.execute("SELECT count(*) FROM ingestion_run").fetchone()[0] == 0  # no orphan 'running' run
    rep = _load(conn, stores=["TX_9"])
    assert rep.status == "rejected" and "no rows" in rep.failure_reason
