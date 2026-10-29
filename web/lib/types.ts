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
  scope: {
    partial: boolean;
    stores?: string[];
    limit_series?: number;
    batch?: { id: string; part: number; of: number };
    pg_granularity?: string;
  };
  started_at: string;
  finished_at: string | null;
}

export interface Overview {
  exposure: {
    as_of: string;
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
  store: string | null;
  raw_reason: string | null;
  verdict: "valid" | "invalid" | "partially_valid" | "needs_evidence" | null;
  rule: string | null;
  supported_amount: Decimal | null;
  recoverable_amount: Decimal | null;
  priority_rank: number | null;
}

export interface DeductionDetail extends Deduction {
  engine_version: string | null;
  evidence: Record<string, unknown> | null;
  calculation_id: number | null;
  assessed_at: string | null;
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


export interface CashAppItem {
  remittance_line_id: number;
  payment_reference: string;
  payment_date: string;
  raw_invoice_ref: string | null;
  amount: Decimal;
  suggested_invoice: string | null;
  confidence: Decimal | null;
  explanation: Record<string, unknown> | null;
}

export interface CashAppSummary {
  cash_lines: number;
  auto_applied: number;
  in_review: number;
  unmatched: number;
  applied_amount: Decimal;
  unapplied_cash: Decimal;
  review_queue: CashAppItem[];
  unmatched_lines: CashAppItem[];
}

// Rates always travel with their count and 95% Wilson interval.
export interface Rate { k: number; n: number; rate: number | null; ci95: [number, number] | null }

export interface CashAppScore {
  lines: number;
  auto_applied: Rate;
  auto_precision: Rate;
  false_match_rate: Rate;
  sent_to_review: Rate;
  review_suggestion_correct: Rate;
  unmatched: Rate;
  dollars_auto_applied_correctly: { amount: Decimal; of_total: Decimal; share: number | null };
  by_noise: Record<string, { n: number; auto_correct: number; auto_wrong: number; review: number; unmatched: number }>;
  precision_coverage_curve: { threshold: number; coverage: number | null; precision: number | null; wrong: number }[];
}

export interface PolicyResult {
  recovered: Decimal; disputes: number; false_disputes: number;
  recoverable_lost_to_deadline: Decimal; share_of_disputable: number | null;
}

export interface Phase2aReport {
  world: { dataset_version_id: number; description: string; as_of: string } | null;
  cash_application: { full: CashAppScore; exact_only_baseline: CashAppScore; auto_threshold: number };
  deductions: {
    deductions: number;
    invalid_in_truth: number;
    recoverable_exact_to_the_cent: Rate;
    false_approval: Rate;
    false_dispute: Rate;
    needs_evidence: Rate;
    true_recoverable_total: Decimal;
    found_recoverable_total: Decimal;
    by_reason: Record<string, { n: number; verdict_correct: number; recoverable_exact: number; true_recoverable: Decimal }>;
  };
  recovery_simulation: ({ reviews_per_week: number; disputable_recoverable: Decimal } &
    Record<"engine" | "largest_amount" | "oldest_first", PolicyResult>)[];
  assumptions: Record<string, string | number>;
}

export interface EvaluationReport<T = Phase2aReport> {
  report_id: number;
  kind: string;
  code_version: string;
  created_at: string;
  body: T;
}
