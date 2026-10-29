// Mirrors cpgos/api/schemas.py. Money and quantities arrive as strings
// (exact decimals) and are never converted to JavaScript floats.

export type Decimal = string;

export interface IngestionRun {
  run_id: number;
  dataset_key: string;
  file_name: string;
  data_origin: string;
  contract_name: string;
  contract_version: string;
  code_version: string;
  status: "running" | "succeeded" | "rejected" | "failed";
  rows_in: number | null;
  rows_loaded: number | null;
  rows_quarantined: number | null;
  failure_reason: string | null;
  scope: { partial: boolean; stores?: string[]; limit_series?: number };
  started_at: string;
  finished_at: string | null;
}

export interface Overview {
  exposure: {
    open_deductions: number;
    open_deduction_amount: Decimal;
    deductions_due_14d: number;
    unapplied_cash: Decimal;
    proposed_cash_applications: number;
  };
  coverage: {
    dataset_versions: number;
    real_dataset_versions: number;
    sales_observations: number;
    skus: number;
    stores: number;
    source_documents: number;
    quarantined_rows: number;
  };
  recent_runs: IngestionRun[];
}

export interface DatasetStatus {
  key: string;
  title: string;
  origin: string;
  method: string;
  license: string;
  license_verified: boolean;
  license_accepted: boolean;
  files_present: boolean;
  versions_loaded: number;
  landing_page: string;
}

export interface Deduction {
  deduction_id: number;
  claim_number: string;
  partner: string;
  code: string | null;
  code_family: string | null;
  amount: Decimal;
  deduction_date: string;
  dispute_deadline: string | null;
  days_to_deadline: number | null;
  status: string;
}

export interface SeriesKey {
  sku_code: string;
  store: string;
  granularity: "day" | "week";
  category: string | null;
  observations: number;
  total_units: Decimal;
}

export interface SalesSeries {
  sku_code: string;
  store: string;
  granularity: "day" | "week";
  data_origin: string;
  points: {
    obs_date: string;
    units: Decimal;
    shelf_price: Decimal | null;
    promo_flag: boolean | null;
    promo_flag_source: string | null;
  }[];
}
