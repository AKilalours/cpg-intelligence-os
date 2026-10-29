"""HTTP API over the canonical data model.

Every endpoint here is read-only and runs inside a READ ONLY transaction, so
even a bug in a query cannot modify the ledger. Every number returned is
computed from database rows at request time; nothing is hardcoded.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import yaml
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row

from cpgos import config
from cpgos.api import schemas as s
from cpgos.datasets.registry import ACCEPT_PATH, load_registry

app = FastAPI(title="CPG Intelligence OS API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=(config.get("CPGOS_CORS_ORIGINS")
                   or f"http://localhost:{config.get('CPGOS_WEB_PORT', '3100')}").split(","),
    allow_methods=["GET"],
    allow_headers=["*"],
)


def get_conn() -> Iterator[psycopg.Connection]:
    url = config.get("CPGOS_DATABASE_URL")
    if not url:
        raise HTTPException(503, "CPGOS_DATABASE_URL is not set; run `make setup` to create .env")
    with psycopg.connect(url, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        yield conn


@app.get("/api/health", response_model=s.Health)
def health(conn: psycopg.Connection = Depends(get_conn)) -> s.Health:
    names = [r["name"] for r in conn.execute("SELECT name FROM schema_migration ORDER BY name")]
    return s.Health(database="ok", migrations_applied=names)


@app.get("/api/overview", response_model=s.Overview)
def overview(conn: psycopg.Connection = Depends(get_conn)) -> s.Overview:
    exp = conn.execute(
        """SELECT
             count(*) FILTER (WHERE status IN ('open','investigating','disputed')) AS open_deductions,
             coalesce(sum(amount) FILTER (WHERE status IN ('open','investigating','disputed')), 0) AS open_deduction_amount,
             count(*) FILTER (WHERE status IN ('open','investigating')
                              AND dispute_deadline BETWEEN current_date AND current_date + 14) AS deductions_due_14d
           FROM deduction"""
    ).fetchone()
    cash = conn.execute(
        """SELECT
             (SELECT coalesce(sum(amount), 0) FROM payment)
             - (SELECT coalesce(sum(applied_amount), 0) FROM cash_application WHERE status = 'approved')
               AS unapplied_cash,
             (SELECT count(*) FROM cash_application WHERE status = 'proposed') AS proposed_cash_applications"""
    ).fetchone()
    cov = conn.execute(
        """SELECT
             (SELECT count(*) FROM dataset_version) AS dataset_versions,
             (SELECT count(*) FROM dataset_version WHERE data_origin = 'real') AS real_dataset_versions,
             (SELECT count(*) FROM sales_observation) AS sales_observations,
             (SELECT count(*) FROM product) AS skus,
             (SELECT count(*) FROM store) AS stores,
             (SELECT count(*) FROM source_document) AS source_documents,
             (SELECT count(*) FROM quarantined_row) AS quarantined_rows"""
    ).fetchone()
    return s.Overview(
        exposure=s.Exposure(**exp, **cash),
        coverage=s.DataCoverage(**cov),
        recent_runs=_runs(conn, limit=10),
    )


def _runs(conn: psycopg.Connection, limit: int) -> list[s.IngestionRun]:
    rows = conn.execute(
        """SELECT r.run_id, d.dataset_key, d.file_name, d.data_origin, r.contract_name,
                  r.contract_version, r.code_version, r.status, r.rows_in, r.rows_loaded,
                  r.rows_quarantined, r.failure_reason, r.scope, r.started_at, r.finished_at
           FROM ingestion_run r JOIN dataset_version d USING (dataset_version_id)
           ORDER BY r.started_at DESC, r.run_id DESC LIMIT %s""",
        (limit,),
    ).fetchall()
    return [s.IngestionRun(**r) for r in rows]


@app.get("/api/ingestion-runs", response_model=list[s.IngestionRun])
def ingestion_runs(limit: int = Query(50, ge=1, le=500), conn: psycopg.Connection = Depends(get_conn)):
    return _runs(conn, limit)


@app.get("/api/datasets", response_model=list[s.DatasetStatus])
def datasets(conn: psycopg.Connection = Depends(get_conn)) -> list[s.DatasetStatus]:
    accepted = {}
    if ACCEPT_PATH.exists():
        accepted = yaml.safe_load(ACCEPT_PATH.read_text()) or {}
    loaded = dict(
        (r["dataset_key"], r["n"])
        # Test fixtures share dataset keys with the real data they imitate, so they
        # are excluded here: "versions loaded" must never count a fixture as real data.
        for r in conn.execute(
            """SELECT dataset_key, count(*) AS n FROM dataset_version
               WHERE data_origin <> 'test_fixture' GROUP BY dataset_key"""
        )
    )
    out = []
    for key, spec in load_registry().items():
        present = spec.raw_dir.exists() and any(p.is_file() for p in spec.raw_dir.iterdir())
        if spec.method == "bundled":
            present = True
        out.append(s.DatasetStatus(
            key=key, title=spec.title, origin=spec.origin, method=spec.method,
            license=spec.license, license_verified=spec.license_verified,
            license_accepted=accepted.get(key) is True or spec.method == "bundled",
            files_present=present, versions_loaded=loaded.get(key, 0),
            landing_page=spec.landing_page,
        ))
    return out


@app.get("/api/deductions", response_model=list[s.Deduction])
def deductions(
    status: str | None = Query(None, pattern="^[a-z_]+$"),
    limit: int = Query(100, ge=1, le=1000),
    conn: psycopg.Connection = Depends(get_conn),
):
    rows = conn.execute(
        """SELECT d.deduction_id, d.claim_number, p.name AS partner, c.code, c.code_family,
                  d.amount, d.deduction_date, d.dispute_deadline,
                  (d.dispute_deadline - current_date) AS days_to_deadline, d.status
           FROM deduction d
           JOIN trading_partner p USING (partner_id)
           LEFT JOIN deduction_code c USING (deduction_code_id)
           WHERE (%(status)s::text IS NULL OR d.status = %(status)s)
           ORDER BY d.dispute_deadline NULLS LAST, d.amount DESC
           LIMIT %(limit)s""",
        {"status": status, "limit": limit},
    ).fetchall()
    return [s.Deduction(**r) for r in rows]


GRANULARITY = Query(None, pattern="^(day|week)$")


def _granularity(conn: psycopg.Connection, requested: str | None) -> str | None:
    """Never mix daily and weekly rows in one answer: that would double-count units.

    When not requested, use the finest granularity present.
    """
    if requested:
        return requested
    row = conn.execute(
        """SELECT granularity FROM sales_observation GROUP BY granularity
           ORDER BY (granularity = 'day') DESC LIMIT 1"""
    ).fetchone()
    return row["granularity"] if row else None


@app.get("/api/sales/series-keys", response_model=list[s.SeriesKey])
def series_keys(
    limit: int = Query(50, ge=1, le=500),
    granularity: str | None = GRANULARITY,
    conn: psycopg.Connection = Depends(get_conn),
):
    g = _granularity(conn, granularity)
    if g is None:
        return []
    rows = conn.execute(
        """SELECT p.sku_code, st.external_code AS store, o.granularity, p.category,
                  count(*) AS observations, sum(o.units) AS total_units
           FROM sales_observation o
           JOIN product p USING (sku_id) JOIN store st USING (store_id)
           WHERE o.granularity = %s
           GROUP BY p.sku_code, st.external_code, o.granularity, p.category
           ORDER BY total_units DESC LIMIT %s""",
        (g, limit),
    ).fetchall()
    return [s.SeriesKey(**r) for r in rows]


@app.get("/api/sales/series", response_model=s.SalesSeries)
def sales_series(
    sku_code: str = Query(..., max_length=64),
    store: str = Query(..., max_length=32),
    granularity: str | None = GRANULARITY,
    conn: psycopg.Connection = Depends(get_conn),
):
    g = _granularity(conn, granularity)
    rows = conn.execute(
        """SELECT o.obs_date, o.units, o.shelf_price, o.promo_flag, o.promo_flag_source, d.data_origin
           FROM sales_observation o
           JOIN product p USING (sku_id) JOIN store st USING (store_id)
           JOIN dataset_version d USING (dataset_version_id)
           WHERE p.sku_code = %s AND st.external_code = %s AND o.granularity = %s
           ORDER BY o.obs_date""",
        (sku_code, store, g),
    ).fetchall()
    if not rows:
        raise HTTPException(404, f"No {g or ''} sales for {sku_code} at {store}".replace("  ", " "))
    origins = {r["data_origin"] for r in rows}
    return s.SalesSeries(
        sku_code=sku_code, store=store, granularity=g,
        data_origin=next(iter(origins)) if len(origins) == 1 else "mixed",
        points=[s.SalesPoint(**r) for r in rows],  # extra keys are ignored
    )
