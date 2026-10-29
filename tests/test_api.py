import psycopg
import pytest
from fastapi.testclient import TestClient

from cpgos.api.app import app, get_conn
from cpgos.ingest.m5 import load_m5
from cpgos.ingest.reference import load_deduction_codes
from tests.conftest import FIXTURES


@pytest.fixture
def client(db_url, conn, monkeypatch):
    monkeypatch.setenv("CPGOS_DATABASE_URL", db_url)
    return TestClient(app)


def test_health_lists_migrations(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["migrations_applied"][0] == "0001_provenance.sql"


def test_overview_on_empty_db_is_zero_not_fake(client):
    body = client.get("/api/overview").json()
    assert body["exposure"]["open_deductions"] == 0
    assert body["exposure"]["open_deduction_amount"] == "0"
    assert body["coverage"]["sales_observations"] == 0
    assert body["recent_runs"] == []


def test_overview_reflects_loaded_rows(client, conn):
    load_m5(conn, FIXTURES / "m5", origin="test_fixture", license_="fixture", pg_granularity="day", parquet_root=None)
    load_deduction_codes(conn)
    partner = conn.execute("SELECT partner_id FROM trading_partner WHERE name='KeHE'").fetchone()[0]
    conn.execute(
        """INSERT INTO deduction (claim_number, partner_id, amount, deduction_date, dispute_deadline, deduction_code_id)
           SELECT 'R-4471', %s, 514.80, current_date - 10, current_date + 5, deduction_code_id
           FROM deduction_code WHERE code='WP'""",
        (partner,),
    )
    body = client.get("/api/overview").json()
    assert body["exposure"]["open_deductions"] == 1
    assert body["exposure"]["open_deduction_amount"] == "514.80"  # exact string, not a float
    assert body["exposure"]["deductions_due_14d"] == 1
    assert body["coverage"]["sales_observations"] == 13
    assert body["coverage"]["real_dataset_versions"] == 1  # only the KeHE table is real here
    assert body["recent_runs"][0]["data_origin"] == "test_fixture"
    assert body["recent_runs"][0]["scope"] == {"partial": False}

    ded = client.get("/api/deductions").json()[0]
    assert ded["code"] == "WP" and ded["code_family"] == "promotion" and ded["days_to_deadline"] == 5


def test_sales_series_and_404(client, conn):
    load_m5(conn, FIXTURES / "m5", origin="test_fixture", license_="fixture", pg_granularity="day", parquet_root=None)
    keys = client.get("/api/sales/series-keys").json()
    assert {k["sku_code"] for k in keys} == {"HOBBIES_1_001", "FOODS_3_090"}
    series = client.get("/api/sales/series", params={"sku_code": "FOODS_3_090", "store": "CA_1"}).json()
    assert len(series["points"]) == 3 and series["data_origin"] == "test_fixture"
    assert client.get("/api/sales/series", params={"sku_code": "NOPE_1_001", "store": "CA_1"}).status_code == 404


def test_datasets_report_license_and_presence(client):
    rows = {d["key"]: d for d in client.get("/api/datasets").json()}
    assert rows["kehe_deduction_codes"]["files_present"] is True
    assert rows["docile"]["method"] == "manual"


def test_api_connection_is_read_only(db_url, monkeypatch):
    monkeypatch.setenv("CPGOS_DATABASE_URL", db_url)
    gen = get_conn()
    conn = next(gen)
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        conn.execute("INSERT INTO brand (name) VALUES ('should fail')")
    gen.close()


def test_bad_query_params_rejected(client):
    assert client.get("/api/deductions", params={"status": "open; DROP TABLE x"}).status_code == 422


def test_fixture_loads_never_count_as_real_versions(client, conn):
    load_m5(conn, FIXTURES / "m5", origin="test_fixture", license_="fixture", pg_granularity="day", parquet_root=None)
    rows = {d["key"]: d for d in client.get("/api/datasets").json()}
    assert rows["m5"]["versions_loaded"] == 0


def test_daily_and_weekly_rows_are_never_mixed(client, conn):
    load_m5(conn, FIXTURES / "m5", origin="test_fixture", license_="fixture", pg_granularity="day", parquet_root=None)
    load_m5(conn, FIXTURES / "m5", origin="test_fixture", license_="fixture", pg_granularity="week", parquet_root=None)
    day = {k["sku_code"]: k["total_units"] for k in client.get("/api/sales/series-keys").json()}
    week = {k["sku_code"]: k["total_units"] for k in client.get("/api/sales/series-keys", params={"granularity": "week"}).json()}
    assert day == week == {"FOODS_3_090": "15.000", "HOBBIES_1_001": "7.000"}  # not doubled
    s = client.get("/api/sales/series", params={"sku_code": "HOBBIES_1_001", "store": "CA_1", "granularity": "week"}).json()
    assert s["granularity"] == "week" and len(s["points"]) == 2


def test_empty_sales_returns_no_keys(client):
    assert client.get("/api/sales/series-keys").json() == []
