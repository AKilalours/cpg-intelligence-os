"""API response models.

Money is typed as Decimal and serialized as a JSON string ("1234.50"), never
a float, so the browser receives exactly what the ledger holds.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class Health(BaseModel):
    database: str
    migrations_applied: list[str]


class Exposure(BaseModel):
    open_deductions: int
    open_deduction_amount: Decimal
    deductions_due_14d: int
    unapplied_cash: Decimal
    proposed_cash_applications: int


class DataCoverage(BaseModel):
    dataset_versions: int
    real_dataset_versions: int
    sales_observations: int
    skus: int
    stores: int
    source_documents: int
    quarantined_rows: int


class IngestionRun(BaseModel):
    run_id: int
    dataset_key: str
    file_name: str
    data_origin: str
    contract_name: str
    contract_version: str
    code_version: str
    status: str
    rows_in: int | None
    rows_loaded: int | None
    rows_quarantined: int | None
    failure_reason: str | None
    scope: dict
    started_at: datetime
    finished_at: datetime | None


class Overview(BaseModel):
    exposure: Exposure
    coverage: DataCoverage
    recent_runs: list[IngestionRun]


class DatasetStatus(BaseModel):
    key: str
    title: str
    origin: str
    method: str
    license: str
    license_verified: bool
    license_accepted: bool
    files_present: bool
    versions_loaded: int
    landing_page: str


class Deduction(BaseModel):
    deduction_id: int
    claim_number: str
    partner: str
    code: str | None
    code_family: str | None
    amount: Decimal
    deduction_date: date
    dispute_deadline: date | None
    days_to_deadline: int | None
    status: str


class SalesPoint(BaseModel):
    obs_date: date
    units: Decimal
    shelf_price: Decimal | None
    promo_flag: bool | None
    promo_flag_source: str | None


class SalesSeries(BaseModel):
    sku_code: str
    store: str
    granularity: str
    data_origin: str
    points: list[SalesPoint]


class SeriesKey(BaseModel):
    sku_code: str
    store: str
    granularity: str
    category: str | None
    observations: int
    total_units: Decimal
