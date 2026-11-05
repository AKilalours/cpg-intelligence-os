"""HTTP API over the canonical data model.

Read endpoints run inside a READ ONLY transaction, so even a bug in a query
cannot modify the ledger. The only write endpoints are human reviews; they
run as the database's reviewer role, which can record reviews, remembered
corrections, and audit events, and nothing else. Every number returned is
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
from cpgos.finance import deductions as ded

app = FastAPI(title="CPG Intelligence OS API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=(config.get("CPGOS_CORS_ORIGINS")
                   or f"http://localhost:{config.get('CPGOS_WEB_PORT', '3100')}").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


DB_DOWN = "The database isn't running. Start it with: make db"


@app.exception_handler(psycopg.OperationalError)
def database_unreachable(_request, exc: psycopg.OperationalError):
    """A stopped database used to surface as a bare 500 on every page. Say what is wrong and how to fix it."""
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=503, content={"detail": DB_DOWN})


def get_write_conn() -> Iterator[psycopg.Connection]:
    """For human reviews only; the review code switches to the reviewer role inside its transaction."""
    url = config.get("CPGOS_DATABASE_URL")
    if not url:
        raise HTTPException(503, "CPGOS_DATABASE_URL is not set; run `make setup` to create .env")
    with psycopg.connect(url, autocommit=True) as conn:
        yield conn


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


def _as_of(conn: psycopg.Connection):
    """Deadlines are measured against the financial world's clock, not the wall clock:
    the world ends in 2016, so 'due in 14 days' against today would always be zero."""
    return conn.execute("SELECT coalesce(max(as_of_date), current_date) AS d FROM world_clock").fetchone()["d"]


@app.get("/api/overview", response_model=s.Overview)
def overview(conn: psycopg.Connection = Depends(get_conn)) -> s.Overview:
    as_of = _as_of(conn)
    exp = conn.execute(
        """SELECT
             count(*) FILTER (WHERE status IN ('open','investigating','disputed')) AS open_deductions,
             coalesce(sum(amount) FILTER (WHERE status IN ('open','investigating','disputed')), 0) AS open_deduction_amount,
             count(*) FILTER (WHERE status IN ('open','investigating')
                              AND dispute_deadline BETWEEN %(d)s AND %(d)s::date + 14) AS deductions_due_14d
           FROM deduction""", {"d": as_of}
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
             (SELECT coalesce(sum(observations), 0) FROM sales_series_stats) AS sales_observations,
             (SELECT count(*) FROM product) AS skus,
             (SELECT count(*) FROM store) AS stores,
             (SELECT count(*) FROM source_document) AS source_documents,
             (SELECT count(*) FROM quarantined_row) AS quarantined_rows"""
    ).fetchone()
    return s.Overview(
        exposure=s.Exposure(as_of=as_of, **exp, **cash),
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
        # Look inside subfolders: CORD keeps its files in cord/data/ and cord/images/ (shown as "Missing" before).
        present = spec.raw_dir.exists() and any(p.is_file() for p in spec.raw_dir.rglob("*"))
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


_DEDUCTION_SQL = """
    SELECT d.deduction_id, d.claim_number, p.name AS partner, c.code, c.code_family,
           d.amount, d.deduction_date, d.dispute_deadline,
           (d.dispute_deadline - %(as_of)s::date) AS days_to_deadline, d.status,
           st.external_code AS store, d.raw_reason,
           a.verdict, a.rule, a.supported_amount, a.recoverable_amount,
           a.engine_version, a.evidence, a.calculation_id, a.created_at AS assessed_at
    FROM deduction d
    JOIN trading_partner p USING (partner_id)
    LEFT JOIN deduction_code c USING (deduction_code_id)
    LEFT JOIN store st ON st.store_id = d.store_id
    LEFT JOIN LATERAL (SELECT * FROM deduction_assessment x WHERE x.deduction_id = d.deduction_id
                       ORDER BY x.assessment_id DESC LIMIT 1) a ON true
"""


@app.get("/api/deductions", response_model=list[s.Deduction])
def deductions(
    status: str | None = Query(None, pattern="^[a-z_]+$"),
    verdict: str | None = Query(None, pattern="^[a-z_]+$"),
    order: str = Query("priority", pattern="^(priority|deadline|amount)$"),
    limit: int = Query(100, ge=1, le=1000),
    conn: psycopg.Connection = Depends(get_conn),
):
    """The deduction queue. order=priority uses the engine's own queue rule
    (cpgos.finance.deductions.priority_key), so the UI and the evaluation rank
    claims identically."""
    as_of = _as_of(conn)
    rows = conn.execute(
        _DEDUCTION_SQL + """
        WHERE (%(status)s::text IS NULL OR d.status = %(status)s)
          AND (%(verdict)s::text IS NULL OR a.verdict = %(verdict)s)""",
        {"status": status, "verdict": verdict, "as_of": as_of},
    ).fetchall()

    def as_assessment(r):
        return ded.Assessment(r["deduction_id"], r["claim_number"], r["code"] or "", r["raw_reason"] or "", r["amount"],
                              r["deduction_date"], r["dispute_deadline"], r["verdict"] or "needs_evidence", r["rule"] or "",
                              r["supported_amount"], r["recoverable_amount"] or 0)

    ranked = {a.deduction_id: i for i, a in enumerate(ded.queue([as_assessment(r) for r in rows], as_of), start=1)}
    for r in rows:
        r["priority_rank"] = ranked[r["deduction_id"]] if r["verdict"] else None
    if order == "priority":
        rows.sort(key=lambda r: (r["priority_rank"] is None, r["priority_rank"] or 0))
    elif order == "deadline":
        rows.sort(key=lambda r: (r["dispute_deadline"] is None, r["dispute_deadline"] or as_of, -r["amount"]))
    else:
        rows.sort(key=lambda r: (-r["amount"], r["deduction_id"]))
    return [s.Deduction(**r) for r in rows[:limit]]


@app.get("/api/deductions/{deduction_id}", response_model=s.DeductionDetail)
def deduction_detail(deduction_id: int, conn: psycopg.Connection = Depends(get_conn)):
    r = conn.execute(_DEDUCTION_SQL + " WHERE d.deduction_id = %(id)s",
                     {"id": deduction_id, "as_of": _as_of(conn)}).fetchone()
    if r is None:
        raise HTTPException(404, f"No deduction {deduction_id}")
    return s.DeductionDetail(**r)


@app.get("/api/finance/cash-application", response_model=s.CashAppSummary)
def cash_application(limit: int = Query(50, ge=1, le=500), conn: psycopg.Connection = Depends(get_conn)):
    counts = conn.execute(
        """WITH cash AS (
               SELECT r.remittance_line_id FROM remittance_line r
               WHERE r.raw_code IS NULL AND r.deduction_amount = 0 AND r.net_amount > 0),
           latest AS (
               SELECT DISTINCT ON (remittance_line_id) remittance_line_id, cash_application_id, status, applied_amount
               FROM cash_application ORDER BY remittance_line_id, cash_application_id DESC),
           people AS (
               SELECT (payload->>'cash_application_id')::bigint AS cash_application_id, action FROM audit_event
               WHERE actor_type = 'human' AND action IN ('cash_approve', 'cash_reject'))
           SELECT count(*) AS cash_lines,
                  count(*) FILTER (WHERE l.status = 'approved' AND p.action IS NULL) AS auto_applied,
                  count(*) FILTER (WHERE p.action = 'cash_approve') AS approved_by_person,
                  count(*) FILTER (WHERE p.action = 'cash_reject') AS rejected_by_person,
                  count(*) FILTER (WHERE l.status = 'proposed') AS in_review,
                  count(*) FILTER (WHERE l.status IS NULL OR l.status NOT IN ('approved', 'proposed')) AS unmatched,
                  coalesce(sum(l.applied_amount) FILTER (WHERE l.status = 'approved'), 0) AS applied_amount
           FROM cash c LEFT JOIN latest l USING (remittance_line_id)
           LEFT JOIN people p ON p.cash_application_id = l.cash_application_id""").fetchone()
    unapplied = conn.execute(
        """SELECT (SELECT coalesce(sum(amount), 0) FROM payment)
                - (SELECT coalesce(sum(applied_amount), 0) FROM cash_application WHERE status = 'approved') AS u""").fetchone()["u"]
    review = conn.execute(
        """SELECT r.remittance_line_id, p.payment_reference, p.payment_date, r.raw_invoice_ref, r.net_amount AS amount,
                  i.invoice_number AS suggested_invoice, c.confidence, c.match_explanation AS explanation,
                  i.total_amount AS invoice_amount, i.invoice_date
           FROM cash_application c
           JOIN remittance_line r USING (remittance_line_id) JOIN payment p USING (payment_id)
           LEFT JOIN invoice i USING (invoice_id)
           WHERE c.status = 'proposed'
           ORDER BY r.net_amount DESC LIMIT %s""", (limit,)).fetchall()
    unmatched = conn.execute(
        """SELECT r.remittance_line_id, p.payment_reference, p.payment_date, r.raw_invoice_ref, r.net_amount AS amount,
                  NULL AS suggested_invoice, NULL::numeric AS confidence, NULL::jsonb AS explanation
           FROM remittance_line r JOIN payment p USING (payment_id)
           WHERE r.raw_code IS NULL AND r.deduction_amount = 0 AND r.net_amount > 0
             AND NOT EXISTS (SELECT 1 FROM cash_application c WHERE c.remittance_line_id = r.remittance_line_id
                             AND c.status IN ('approved', 'proposed'))
           ORDER BY r.net_amount DESC LIMIT %s""", (limit,)).fetchall()
    return s.CashAppSummary(**counts, unapplied_cash=unapplied,
                            review_queue=[s.CashAppItem(**r) for r in review],
                            unmatched_lines=[s.CashAppItem(**r) for r in unmatched])


@app.get("/api/evaluation/latest", response_model=s.EvaluationReport | None)
def latest_evaluation(kind: str = Query("phase2a", pattern="^[a-z0-9_]+$"), conn: psycopg.Connection = Depends(get_conn)):
    """The most recent stored evaluation. Produced by `make eval-recon`; never computed here,
    so a page load cannot run the engines or touch the answer key beyond reading a report."""
    r = conn.execute(
        """SELECT report_id, kind, code_version, created_at, body FROM eval.report
           WHERE kind = %s ORDER BY report_id DESC LIMIT 1""", (kind,)).fetchone()
    if r and kind == "phase2a":
        from pydantic import ValidationError
        try:
            s.Phase2aDeductions.model_validate(r["body"].get("deductions") or {})
        except ValidationError:
            raise HTTPException(409, f"report {r['report_id']} was written by older code; run: make eval-recon") from None
    return s.EvaluationReport(**r) if r else None


GRANULARITY = Query(None, pattern="^(day|week)$")


def _granularity(conn: psycopg.Connection, requested: str | None) -> str | None:
    """Never mix daily and weekly rows in one answer: that would double-count units.

    When not requested, use the finest granularity present.
    """
    if requested:
        return requested
    row = conn.execute(
        """SELECT granularity FROM sales_series_stats GROUP BY granularity
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
        """SELECT p.sku_code, st.external_code AS store, x.granularity, p.category,
                  x.observations, x.total_units
           FROM sales_series_stats x
           JOIN product p USING (sku_id) JOIN store st USING (store_id)
           WHERE x.granularity = %s
           ORDER BY x.total_units DESC LIMIT %s""",
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


# ---------- document review (fix 4) ----------

from pathlib import Path  # noqa: E402

from fastapi.responses import FileResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from cpgos.documents import review  # noqa: E402

RAW_ROOT = Path(config.get("CPGOS_RAW_ROOT") or Path(__file__).resolve().parents[2] / "data" / "raw")


class ReviewIn(BaseModel):
    action: str
    reviewer: str
    value: str | None = None
    note: str | None = None


@app.get("/api/documents/regression")
def documents_regression(conn: psycopg.Connection = Depends(get_conn)):
    """The current routing rule replayed on every value a person confirmed (pure replay, no model calls)."""
    from cpgos.documents import regression
    out = regression.summary(regression.check(regression.cases(conn)))
    out["not_replayable_yet"] = regression.without_inputs(conn)
    return out


@app.get("/api/documents/queue")
def document_queue(limit: int = Query(50, ge=1, le=500), conn: psycopg.Connection = Depends(get_conn)):
    return review.queue(conn, limit)


@app.get("/api/documents/{document_id}")
def document_detail(document_id: int, conn: psycopg.Connection = Depends(get_conn)):
    v = review.document_view(conn, document_id)
    if v is None:
        raise HTTPException(404, f"No document {document_id}")
    v["image_url"] = f"/api/documents/{document_id}/image"
    return v


@app.get("/api/documents/{document_id}/image")
def document_image(document_id: int, conn: psycopg.Connection = Depends(get_conn)):
    r = conn.execute("SELECT storage_key, mime_type FROM source_document WHERE source_document_id = %s",
                     (document_id,)).fetchone()
    if r is None:
        raise HTTPException(404, f"No document {document_id}")
    path = (RAW_ROOT / r["storage_key"]).resolve()
    if not path.is_relative_to(RAW_ROOT.resolve()) or not path.is_file():   # storage keys never leave data/raw
        raise HTTPException(404, "document image not available on this machine")
    return FileResponse(path, media_type=r["mime_type"] or "application/octet-stream")


@app.post("/api/documents/{document_id}/fields/{field_name}/review")
def document_review(document_id: int, field_name: str, body: ReviewIn,
                    conn: psycopg.Connection = Depends(get_write_conn)):
    try:
        return review.submit(conn, document_id, field_name, body.action, body.reviewer, body.value, body.note)
    except review.ReviewError as e:
        raise HTTPException(400, str(e)) from None


@app.get("/api/evaluation/routing-history")
def routing_history(conn: psycopg.Connection = Depends(get_conn)):
    """Every complete one-time holdout score of the invoice routing rule, oldest first: the story of the rule."""
    out = []
    for r in conn.execute("""SELECT report_id, created_at, body FROM eval.report WHERE kind = 'routing_holdout'
                             ORDER BY report_id"""):
        b = r["body"]
        w = b.get("with_third_reader") or b.get("with_claude") or {}
        if not isinstance(w, dict) or "accepted_correct" not in w:
            continue                                   # incomplete runs are never shown as results
        out.append({"report_id": r["report_id"], "created_at": r["created_at"], "policy": b.get("policy"),
                    "rounds": b.get("rounds"), "invoices": b.get("invoices"),
                    "accepted_correct": w["accepted_correct"], "straight_through": w["straight_through"],
                    "straight_through_complete": w.get("straight_through_complete_invoices"),
                    "review_fields_per_invoice": w.get("review_fields_per_invoice"),
                    "wrong": (w["accepted_correct"]["n"] - w["accepted_correct"]["k"])})
    # One result per rule version: the latest complete score of each.
    latest = {}
    for x in out:
        latest[x["policy"]] = x
    return sorted(latest.values(), key=lambda x: x["report_id"])


# ---------- finance decisions (fix 4) ----------

from cpgos.finance import review as finance_review  # noqa: E402


class DecisionIn(BaseModel):
    action: str
    reviewer: str
    note: str | None = None


@app.post("/api/cash/{remittance_line_id}/review")
def cash_review(remittance_line_id: int, body: DecisionIn, conn: psycopg.Connection = Depends(get_write_conn)):
    try:
        return finance_review.decide_cash(conn, remittance_line_id, body.action, body.reviewer, body.note)
    except finance_review.DecisionError as e:
        raise HTTPException(400, str(e)) from None


@app.post("/api/deductions/{deduction_id}/review")
def deduction_review(deduction_id: int, body: DecisionIn, conn: psycopg.Connection = Depends(get_write_conn)):
    try:
        return finance_review.decide_deduction(conn, deduction_id, body.action, body.reviewer, body.note)
    except finance_review.DecisionError as e:
        raise HTTPException(400, str(e)) from None


# ---------- Today: money flow ----------

@app.get("/api/overview/flow", response_model=s.MoneyFlow)
def money_flow(conn: psycopg.Connection = Depends(get_conn)) -> s.MoneyFlow:
    """Cash paid splits by who posted it; deductions taken split by what is left to do. Disjoint streams."""
    as_of = _as_of(conn)
    cash = conn.execute(
        """WITH latest AS (
               SELECT DISTINCT ON (remittance_line_id) remittance_line_id, cash_application_id, status
               FROM cash_application ORDER BY remittance_line_id, cash_application_id DESC),
           people AS (
               SELECT DISTINCT (payload->>'cash_application_id')::bigint AS cash_application_id FROM audit_event
               WHERE actor_type = 'human' AND action = 'cash_approve'),
           lines AS (
               SELECT r.net_amount AS amount,
                      CASE WHEN l.status = 'approved' AND p.cash_application_id IS NULL THEN 'engine'
                           WHEN l.status = 'approved' THEN 'person'
                           WHEN l.status = 'proposed' THEN 'waiting'
                           ELSE 'unmatched' END AS key
               FROM remittance_line r LEFT JOIN latest l USING (remittance_line_id)
               LEFT JOIN people p ON p.cash_application_id = l.cash_application_id
               WHERE r.raw_code IS NULL AND r.deduction_amount = 0 AND r.net_amount > 0)
           SELECT key, coalesce(sum(amount), 0) AS amount, count(*) AS count FROM lines GROUP BY key""").fetchall()
    ded = conn.execute(
        """WITH a AS (
               SELECT DISTINCT ON (deduction_id) deduction_id, verdict, recoverable_amount
               FROM deduction_assessment ORDER BY deduction_id, assessment_id DESC),
           d AS (
               SELECT d.amount, a.recoverable_amount,
                      CASE WHEN d.status NOT IN ('open', 'investigating') THEN 'decided'
                           WHEN d.dispute_deadline < %s THEN 'closed'
                           WHEN a.verdict IN ('invalid', 'partially_valid') THEN 'dispute'
                           WHEN a.verdict = 'valid' THEN 'valid'
                           ELSE 'evidence' END AS key
               FROM deduction d LEFT JOIN a USING (deduction_id))
           SELECT key, coalesce(sum(amount), 0) AS amount, count(*) AS count,
                  coalesce(sum(recoverable_amount), 0) AS recoverable FROM d GROUP BY key""", (as_of,)).fetchall()
    order_c = ["engine", "person", "waiting", "unmatched"]
    order_d = ["dispute", "evidence", "valid", "closed", "decided"]
    return s.MoneyFlow(as_of=as_of,
                       cash=sorted([s.FlowStream(**r) for r in cash], key=lambda x: order_c.index(x.key)),
                       deductions=sorted([s.FlowStream(**r) for r in ded], key=lambda x: order_d.index(x.key)))

