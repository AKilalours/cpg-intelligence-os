import csv
import shutil

from cpgos.ingest.m5 import load_m5
from tests.conftest import FIXTURES

M5 = FIXTURES / "m5"


def _load(conn, path=M5):
    return load_m5(conn, path, origin="test_fixture", license_="fixture")


def test_load_counts_and_prelaunch_skip(conn):
    rep = _load(conn)
    assert rep.status == "succeeded"
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
    for f in M5.glob("*.csv"):
        shutil.copy(f, tmp_path / f.name)
    p = tmp_path / "sales_train_evaluation.csv"
    rows = list(csv.reader(p.open()))
    rows[1][-1] = "9"  # correct one day's units for HOBBIES_1_001
    with p.open("w", newline="") as f:
        csv.writer(f).writerows(rows)

    rep = _load(conn, tmp_path)
    assert rep.rows_loaded == 1
    units, _ = conn.execute(
        """SELECT s.units, d.sha256 FROM sales_observation s
           JOIN product p USING (sku_id) JOIN dataset_version d USING (dataset_version_id)
           WHERE p.sku_code='HOBBIES_1_001' AND s.obs_date='2011-02-07'"""
    ).fetchone()
    assert units == 9
    versions = conn.execute(
        "SELECT count(DISTINCT sha256) FROM dataset_version WHERE file_name='sales_train_evaluation.csv'"
    ).fetchone()[0]
    assert versions == 2


def test_corrupt_batch_is_rejected_and_nothing_loads(conn, tmp_path):
    for f in M5.glob("*.csv"):
        shutil.copy(f, tmp_path / f.name)
    p = tmp_path / "sales_train_evaluation.csv"
    rows = list(csv.reader(p.open()))
    rows[1][8] = "-3"  # negative units: impossible
    with p.open("w", newline="") as f:
        csv.writer(f).writerows(rows)

    rep = _load(conn, tmp_path)
    assert rep.status == "rejected" and rep.rows_quarantined == 1
    assert conn.execute("SELECT count(*) FROM sales_observation").fetchone()[0] == 0
    status, reason = conn.execute("SELECT status, failure_reason FROM ingestion_run").fetchone()
    assert status == "rejected" and "quarantine rate" in reason
    violations = conn.execute("SELECT violations FROM quarantined_row").fetchone()[0]
    assert "units" in violations[0]
