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
  invoice_amount?: Decimal | null;
  invoice_date?: string | null;
}

export interface CashAppSummary {
  cash_lines: number;
  auto_applied: number;
  approved_by_person: number;
  rejected_by_person: number;
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

// Mirrors cpgos/api/schemas.py Phase2aDeductions (validated by the API, tested in tests/test_api.py).
export interface DeductionScore {
  deductions: number;
  needs_evidence: Rate;
  recoverable_exact_to_the_cent_when_decided: Rate;
  false_approval: Rate;
  false_dispute: Rate;
  true_recoverable_total: Decimal;
  found_recoverable_total: Decimal;
}

export interface RegionScore extends DeductionScore {
  regions: string[];
}

export interface Phase2aReport {
  world: { dataset_version_id: number; description: string; as_of: string } | null;
  cash_application: { full: CashAppScore; exact_only_baseline: CashAppScore; auto_threshold: number };
  deductions: DeductionScore & {
    design_region: RegionScore;
    report_region: RegionScore;
    mistakes: { claim: string; rule: string; truth: string; verdict: string; region: string | null }[];
    by_reason: Record<string, { n: number; verdict_correct: number; needs_evidence: number; true_recoverable: Decimal }>;
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

// ---------- document review (fix 4) ----------

export interface ReviewQueueItem {
  source_document_id: number;
  policy_version: string;
  open_fields: number;
  accepted_fields: number;
  review_fields: string[];
  boxes?: { field: string; status: "review" | "accepted" | "done"; bbox: number[] }[];
}

export interface FieldReview {
  action: "approve" | "correct";
  value: string | null;
  reviewer: string;
  at: string;
}

export interface FieldDecision {
  field: string;
  kind: string | null;
  status: "accepted" | "review";
  value: string | null;
  reason: string;
  candidates: Record<string, string>;
  bbox: [number, number, number, number] | null; // [x0, y0, x1, y1] in [0, 1], top-left origin
  review: FieldReview | null;
}

export interface DocumentView {
  source_document_id: number;
  storage_key: string;
  source_type: string;
  mime_type: string | null;
  policy_version: string | null;
  fields: FieldDecision[];
  open_fields: number;
  image_url: string;
  readings: Reading[];
}

export interface Reading {
  reader: "rules" | "local_model" | "claude";
  field: string;
  value: string | null;
  bbox: [number, number, number, number] | null;
}

export interface FlowStream { key: string; amount: Decimal; count: number; recoverable: Decimal | null }
export interface MoneyFlow { as_of: string | null; cash: FlowStream[]; deductions: FlowStream[] }

export interface RegressionGate {
  policy: string; confirmed_values: number; kept_right: number; asks_a_person: number;
  open_mistakes: number; regressions: number; corrections: number; corrections_now_accepted_right: number;
  passed: boolean; not_replayable_yet: number;
  wrong: { doc: number; field: string; truth: string | null; now_accepts: string | null; kind: "regression" | "open_mistake"; reviewed_under: string }[];
}

export interface CaseRow {
  case_id: number; deduction_id: number; status: string; claim_number: string; amount: Decimal;
  deduction_date: string; dispute_deadline: string | null; deduction_status: string; code: string;
  raw_reason: string | null; agent_action: "accept" | "dispute" | "request"; agent_reason: string;
  agent_request: string | null; recoverable: string | null; engine_verdict: string; engine_rule: string;
  agent_version: string; human_action: string | null; reviewer: string | null; differs_from_engine: boolean;
  as_of: string; window_open: boolean;
}
export interface ToolCall { tool: string; params?: string[]; rows?: number; kind?: string; calculation_id?: number }
export interface CaseDetail extends CaseRow {
  finding: { action: string; reason: string; request: string | null; recoverable: string;
             facts: { fact: string; value: unknown }[]; tool_calls: ToolCall[] };
  calculations: { calculation_id: number; kind: string; formula_version: string; inputs: Record<string, unknown>;
                  result: string; computed_by: string; created_at: string }[];
  trail: { audit_event_id: number; actor_type: string; actor_id: string; action: string; created_at: string;
           payload: Record<string, unknown> }[];
  claim: Record<string, unknown>;
  letter: { version: string; source: string; text: string; unsupported: string[]; model?: string;
            model_rejected?: { unsupported_numbers: string[]; window_problems?: string[] }; window_problems?: string[] };
}

export interface StpLane {
  lane: string; description: string; mode: "off" | "enforce"; max_error_rate: number; confidence: number;
  audit_rate: number; window_size: number; reviews: number; errors: number; upper_bound: number;
  reversals: number; is_open: boolean; reviews_needed: number; routed: Record<string, number>;
  updated_by: string; updated_at: string;
}
export interface StpStatus { engine_version: string; lanes: StpLane[] }
export interface StpPoint { day: string; wrong_posted: number }

export interface DriftAlarm {
  drift_alarm_id: number; stream: "cash" | "deductions" | "prices"; feature: string; checked_through: string;
  p_value: number; psi: number; moved: { value: string; baseline: number; now: number }[]; raised_at: string;
}
export interface DriftStatus {
  open: DriftAlarm[]; holds_cash: boolean;
  evaluation: null | { report_id: number; kind: string;
    false_alarms: Record<string, { weeks_checked: number; weeks_with_an_alarm: number; episodes?: number; years_checked?: number }> | null;
    detection: Record<string, { stream: string; rolls: number; detected: number; days_to_alarm: { median: number | null } }> };
}

export interface ForecastBrief { item: string; store: string; dept: string; class: string; class_scored: string; dollars: number;
  lgbm_rmsse: number | null; ses_rmsse: number | null }
export interface ForecastSeries extends ForecastBrief {
  weeks: string[]; future_weeks: string[]; history: (number | null)[]; actual: (number | null)[]; price: (number | null)[];
  test: Record<string, (number | null)[]>; test_rmsse: Record<string, number | null>;
  test_lo: (number | null)[]; test_hi: (number | null)[]; future: (number | null)[]; future_lo: (number | null)[]; future_hi: (number | null)[];
}
export interface ForecastSummary {
  version: string; gbm: string; series: number; classes: Record<string, number>; stores: string[];
  test_weeks: string[]; future_weeks: string[];
  test: null | { paired: Record<string, { difference: number; ci95: number[]; relative: number }>;
    scores: Record<string, { wrmsse: number }> };
  intervals: null | { test_coverage: number; test_coverage_by_class: Record<string, number> };
}

export interface PromoEvidence {
  dept: string; count: number; depts: Record<string, number>;
  depth: number[]; lift: number[]; week: string[]; item: string[]; store: string[]; temporary: boolean[];
  test: null | { test_cuts: number; mae: Record<string, number>; show_model: boolean; show_history: boolean;
    median_actual: number; median_model: number; share_actual_positive: number };
}

export interface ProofCard {
  key: string; title: string; href: string; method: string;
  visual: { type: "ring"; k: number; n: number; wrong?: number; note?: string; target?: number }
    | { type: "dots"; left: number; left_label: string; right: number; right_label: string; p: number }
    | { type: "versus"; left: number; left_label: string; right: number; right_label: string; lower_better?: boolean; honest_no?: boolean; unit?: string };
}

export interface CasePicture {
  case_id: number; window?: [string, string];
  claim: { claim_number: string; amount: string; deduction_date: string; dispute_deadline: string; period_start: string | null;
           period_end: string | null; claimed_units: string | null; raw_reason: string; code: string; store_id: number | null };
  money: { claimed: string; supported: string | null; recoverable: string | null };
  engine: { verdict: string; rule: string };
  promotions: { promotion_id: number; store: number; start: string; end: string; allowance: string; this_store: boolean }[];
  prices: { week: string; price: number }[]; drops: string[]; pos: { week: string; units: number | null }[];
}

export interface StpReplay { lane: string; fault_from: string; days: string[]; gate: number[]; no_gate: number[];
  opened: string | null; closed: string | null; to_a_person: number; lines: number; caught: number }
