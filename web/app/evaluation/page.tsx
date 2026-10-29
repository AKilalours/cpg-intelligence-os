import { api } from "@/lib/api";
import { count, money, pct, rateText, when } from "@/lib/format";
import type { CashAppScore, EvaluationReport, IngestionRun, Phase2aReport } from "@/lib/types";
import { Cmd, Figure, Notice, PageHeader, Section, Status } from "@/components/ui";

export const dynamic = "force-dynamic";

const NOISE: Record<string, string> = {
  clean: "Clean reference",
  typo_ref: "Typo in reference",
  missing_ref: "No reference",
  wrong_ref: "Another invoice's number",
  partial: "Short payment",
};

export default async function EvaluationPage() {
  const [report, runs] = await Promise.all([
    api<EvaluationReport | null>("/api/evaluation/latest?kind=phase2a"),
    api<IngestionRun[]>("/api/ingestion-runs?limit=100"),
  ]);
  const rejected = runs.ok ? runs.data.filter((r) => r.status === "rejected") : [];
  return (
    <>
      <PageHeader
        title="Evaluation Lab"
        purpose="Measure every engine against a baseline and a hidden answer key, with the uncertainty shown, before trusting it."
      />
      {!report.ok ? (
        <Notice kind="error" title="Evaluation unavailable">{report.error}</Notice>
      ) : report.data === null ? (
        <Section title="Reconciliation benchmark">
          <Notice kind="empty" title="No evaluation stored yet">
            Run <Cmd>make eval-recon</Cmd> after <Cmd>make finance-world</Cmd>.
          </Notice>
        </Section>
      ) : (
        <Reconciliation r={report.data} />
      )}
      <Section title="Rejected data loads" note="Batches refused by a data contract">
        {!runs.ok ? (
          <Notice kind="error" title="Load history unavailable">{runs.error}</Notice>
        ) : rejected.length === 0 ? (
          <Notice kind="empty" title="No rejected loads">
            When a source changes format, the batch is refused and shows up here with its reason.
          </Notice>
        ) : (
          <table className="ledger bg-sheet">
            <thead><tr><th>Run</th><th>File</th><th>Status</th><th className="num">Quarantined</th><th>Reason</th></tr></thead>
            <tbody>
              {rejected.map((r) => (
                <tr key={r.run_id}>
                  <td>{r.run_id}</td><td>{r.file_name}</td><td><Status value={r.status} /></td>
                  <td className="num">{r.rows_quarantined === null ? "" : count(r.rows_quarantined)}</td>
                  <td>{r.failure_reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
    </>
  );
}

function Reconciliation({ r }: { r: EvaluationReport }) {
  const b: Phase2aReport = r.body;
  const full = b.cash_application.full;
  const base = b.cash_application.exact_only_baseline;
  const d = b.deductions;
  return (
    <>
      <p className="-mt-4 mb-8 max-w-3xl text-sm text-muted">
        Report #{r.report_id}, {when(r.created_at)}, code {r.code_version}. World: {b.world?.description}, ledger date {b.world?.as_of}.
        Data origin: real M5 sales and published KeHE terms, with payments and deductions generated on top
        (real_anchored_generated). The engines run as a database role that cannot read the answer key.
      </p>

      <Section title="Cash application" note={`Automatic posting at confidence ≥ ${b.cash_application.auto_threshold}`}>
        <div className="mb-6 flex flex-wrap divide-x divide-rule border-y border-rule">
          <Figure value={count(full.false_match_rate.k)} label={`Wrong automatic postings (baseline: ${base.false_match_rate.k})`} />
          <Figure value={pct(full.dollars_auto_applied_correctly.share)} label={`Dollars posted correctly, no person (baseline: ${pct(base.dollars_auto_applied_correctly.share)})`} />
          <Figure value={pct(full.unmatched.rate)} label={`Left with no suggestion (baseline: ${pct(base.unmatched.rate)})`} />
        </div>
        <div className="overflow-x-auto">
          <table className="ledger mb-6 bg-sheet">
            <thead><tr><th>Measure</th><th>This engine</th><th>Exact-reference baseline</th></tr></thead>
            <tbody>
              <Row label="Posted automatically" a={full} b={base} k="auto_applied" />
              <Row label="Wrong among automatic postings" a={full} b={base} k="false_match_rate" />
              <Row label="Sent to a person" a={full} b={base} k="sent_to_review" />
              <Row label="Suggestion correct, when sent to a person" a={full} b={base} k="review_suggestion_correct" />
              <Row label="Left unmatched" a={full} b={base} k="unmatched" />
            </tbody>
          </table>
          <h3 className="mb-2 text-sm font-semibold">By the kind of mess on the remittance</h3>
          <table className="ledger mb-6 bg-sheet">
            <thead>
              <tr><th>Remittance line</th><th className="num">Lines</th><th className="num">Auto, right</th><th className="num">Auto, wrong</th><th className="num">To a person</th><th className="num">Unmatched</th><th className="num">Baseline auto, wrong</th></tr>
            </thead>
            <tbody>
              {Object.entries(full.by_noise).map(([k, v]) => (
                <tr key={k}>
                  <td>{NOISE[k] ?? k}</td><td className="num">{v.n}</td><td className="num">{v.auto_correct}</td>
                  <td className={"num " + (v.auto_wrong ? "font-semibold text-debit" : "")}>{v.auto_wrong}</td>
                  <td className="num">{v.review}</td><td className="num">{v.unmatched}</td>
                  <td className={"num " + (base.by_noise[k]?.auto_wrong ? "text-debit" : "")}>{base.by_noise[k]?.auto_wrong ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3 className="mb-2 text-sm font-semibold">Precision against coverage, if the threshold moved</h3>
          <table className="ledger bg-sheet">
            <thead><tr><th className="num">Threshold</th><th className="num">Lines covered</th><th className="num">Precision</th><th className="num">Wrong</th></tr></thead>
            <tbody>
              {full.precision_coverage_curve.map((p) => (
                <tr key={p.threshold} className={p.threshold === b.cash_application.auto_threshold ? "bg-tag/40" : undefined}>
                  <td className="num">{p.threshold.toFixed(2)}</td><td className="num">{pct(p.coverage)}</td>
                  <td className="num">{pct(p.precision)}</td><td className="num">{p.wrong}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section title="Deduction validation" note="Verdicts against the hidden answer key">
        <div className="mb-6 flex flex-wrap divide-x divide-rule border-y border-rule">
          <Figure value={`${d.recoverable_exact_to_the_cent.k}/${d.deductions}`} label="Recoverable amount exact to the cent" />
          <Figure value={count(d.false_approval.k)} label={`Invalid claims accepted (of ${d.false_approval.n})`} />
          <Figure value={count(d.false_dispute.k)} label={`Valid claims disputed (of ${d.false_dispute.n})`} />
          <Figure value={money(d.true_recoverable_total)} label="Recoverable in the answer key" />
        </div>
        <p className="mb-4 max-w-3xl text-sm text-muted">
          Read with care: the validator applies the same published terms the world was generated from, so a perfect
          score shows the evidence lookups and arithmetic are right, not that messy real claims are solved. Intervals:
          false approvals {rateText(d.false_approval)}; false disputes {rateText(d.false_dispute)}.
        </p>
        <table className="ledger bg-sheet">
          <thead><tr><th>Truth</th><th className="num">Claims</th><th className="num">Verdict right</th><th className="num">Amount exact</th><th className="num">Recoverable</th></tr></thead>
          <tbody>
            {Object.entries(d.by_reason).map(([k, v]) => (
              <tr key={k}>
                <td>{k.replace(/_/g, " ")}</td><td className="num">{v.n}</td><td className="num">{v.verdict_correct}</td>
                <td className="num">{v.recoverable_exact}</td><td className="num font-figure text-lg">{money(v.true_recoverable)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section title="Recovery with limited review time" note="Same validator, different order of work">
        <p className="mb-4 max-w-3xl text-sm text-muted">
          Each week an analyst can review a fixed number of claims. Claims below {money(String(b.assumptions.dispute_handling_cost))} are
          not worth a dispute; claims within {b.assumptions.urgent_days} days of their deadline jump the queue.
          Assumption shared by every policy: {b.assumptions.dispute_win}.
        </p>
        <table className="ledger bg-sheet">
          <thead>
            <tr><th className="num">Reviews a week</th><th className="num">Engine order</th><th className="num">Largest first</th><th className="num">Oldest first</th><th className="num">Lost to deadlines (engine / largest / oldest)</th></tr>
          </thead>
          <tbody>
            {b.recovery_simulation.map((s) => (
              <tr key={s.reviews_per_week}>
                <td className="num">{s.reviews_per_week}</td>
                <td className="num font-figure text-lg">{money(s.engine.recovered)} <span className="text-sm text-muted">{pct(s.engine.share_of_disputable, 0)}</span></td>
                <td className="num font-figure text-lg">{money(s.largest_amount.recovered)} <span className="text-sm text-muted">{pct(s.largest_amount.share_of_disputable, 0)}</span></td>
                <td className="num font-figure text-lg">{money(s.oldest_first.recovered)} <span className="text-sm text-muted">{pct(s.oldest_first.share_of_disputable, 0)}</span></td>
                <td className="num">{money(s.engine.recoverable_lost_to_deadline)} / {money(s.largest_amount.recoverable_lost_to_deadline)} / {money(s.oldest_first.recoverable_lost_to_deadline)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
    </>
  );
}

function Row({ label, a, b, k }: { label: string; a: CashAppScore; b: CashAppScore; k: "auto_applied" | "false_match_rate" | "sent_to_review" | "review_suggestion_correct" | "unmatched" }) {
  return (
    <tr>
      <td>{label}</td>
      <td>{rateText(a[k])}</td>
      <td>{rateText(b[k])}</td>
    </tr>
  );
}
