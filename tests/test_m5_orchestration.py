import csv
import shutil

import psycopg
import pytest

import cpgos.ingest.m5 as m5mod
from cpgos.ingest.m5 import LoadInProgressError, dataset_lock, discover_stores, load_m5, load_m5_by_store
from tests.conftest import FIXTURES

KW = dict(origin="test_fixture", license_="fixture", pg_granularity="day", parquet_root=None)


@pytest.fixture
def two_stores(tmp_path):
    """The M5 fixture plus a second store (TX_1) with the same items."""
    for f in (FIXTURES / "m5").glob("*.csv"):
        shutil.copy(f, tmp_path / f.name)
    sales = list(csv.reader((tmp_path / "sales_train_evaluation.csv").open()))
    tx = [[r[0].replace("CA_1", "TX_1"), r[1], r[2], r[3], "TX_1", "TX", *r[6:]] for r in sales[1:]]
    with (tmp_path / "sales_train_evaluation.csv").open("w", newline="") as f:
        csv.writer(f).writerows(sales + tx)
    prices = list(csv.reader((tmp_path / "sell_prices.csv").open()))
    with (tmp_path / "sell_prices.csv").open("w", newline="") as f:
        csv.writer(f).writerows(prices + [["TX_1", *p[1:]] for p in prices[1:]])
    return tmp_path


def _totals(conn):
    return conn.execute("SELECT count(*), sum(units) FROM sales_observation").fetchone()


def test_discover_stores_in_file_order(two_stores):
    assert discover_stores(two_stores / "sales_train_evaluation.csv") == ["CA_1", "TX_1"]


def test_by_store_equals_single_pass(conn, db_url, two_stores):
    reps = load_m5_by_store(conn, two_stores, progress=lambda m: None, **KW)
    assert [r.status for r in reps] == ["succeeded", "succeeded"]
    by_store = _totals(conn)
    conn.execute("TRUNCATE sales_observation")
    load_m5(conn, two_stores, **KW)
    assert _totals(conn) == by_store == (26, 44)
    scopes = [r[0] for r in conn.execute("SELECT scope FROM ingestion_run ORDER BY run_id LIMIT 2")]
    assert [(s["stores"], s["batch"]["part"], s["batch"]["of"]) for s in scopes] == [(["CA_1"], 1, 2), (["TX_1"], 2, 2)]


def test_rerun_resumes_and_skips_finished_stores(conn, two_stores):
    load_m5(conn, two_stores, stores=["CA_1"], **KW)
    msgs = []
    reps = load_m5_by_store(conn, two_stores, progress=msgs.append, **KW)
    assert [r.rows_in for r in reps] == [13]  # only TX_1 was loaded
    assert any("Skipping 1 store" in m for m in msgs)
    assert load_m5_by_store(conn, two_stores, progress=msgs.append, **KW) == []  # nothing left
    assert len(load_m5_by_store(conn, two_stores, force=True, progress=msgs.append, **KW)) == 2


def test_failed_store_stops_the_batch_and_resume_continues(conn, two_stores):
    rows = list(csv.reader((two_stores / "sales_train_evaluation.csv").open()))
    good = [list(r) for r in rows]
    rows[3][8] = "-5"  # TX_1 first item: impossible units
    with (two_stores / "sales_train_evaluation.csv").open("w", newline="") as f:
        csv.writer(f).writerows(rows)
    msgs = []
    reps = load_m5_by_store(conn, two_stores, progress=msgs.append, **KW)
    assert [r.status for r in reps] == ["succeeded", "rejected"]
    assert any("Re-run to resume" in m for m in msgs)
    with (two_stores / "sales_train_evaluation.csv").open("w", newline="") as f:
        csv.writer(f).writerows(good)  # a corrected file has a new fingerprint: both stores reload
    assert [r.status for r in load_m5_by_store(conn, two_stores, progress=msgs.append, **KW)] == ["succeeded"] * 2


def test_interrupt_marks_run_failed_not_running(conn, monkeypatch):
    def boom(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(m5mod, "_write_postgres", boom)
    with pytest.raises(KeyboardInterrupt):
        load_m5(conn, FIXTURES / "m5", **KW)
    assert conn.execute("SELECT status, failure_reason FROM ingestion_run").fetchone() == ("failed", "interrupted")


def test_stale_running_runs_are_closed_by_the_next_load(conn, two_stores):
    load_m5(conn, two_stores, stores=["CA_1"], **KW)
    conn.execute("UPDATE ingestion_run SET status='running', finished_at=NULL")  # as if the process was killed
    msgs = []
    load_m5_by_store(conn, two_stores, progress=msgs.append, **KW)
    first = conn.execute("SELECT status, failure_reason FROM ingestion_run ORDER BY run_id LIMIT 1").fetchone()
    assert first[0] == "failed" and first[1].startswith("interrupted")
    assert any("Marked 1 earlier run" in m for m in msgs)


def test_concurrent_loads_are_refused(conn, db_url, two_stores):
    with psycopg.connect(db_url, autocommit=True) as other, dataset_lock(other, "m5"):
        with pytest.raises(LoadInProgressError):
            load_m5_by_store(conn, two_stores, progress=lambda m: None, **KW)
    assert conn.execute("SELECT count(*) FROM ingestion_run").fetchone()[0] == 0


def test_by_store_refreshes_the_summary_once_at_the_end(conn, two_stores):
    msgs = []
    load_m5_by_store(conn, two_stores, progress=msgs.append, **KW)
    assert sum("Refreshing the per-series summary" in m for m in msgs) == 1
    n, units = conn.execute("SELECT sum(observations), sum(total_units) FROM sales_series_stats").fetchone()
    assert (n, units) == (26, 44)
