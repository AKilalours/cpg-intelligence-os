import { api } from "@/lib/api";
import { count } from "@/lib/format";
import type { IngestionRun } from "@/lib/types";
import { Notice, PageHeader, Section, Status } from "@/components/ui";

export const dynamic = "force-dynamic";

export default async function EvaluationPage() {
  const runs = await api<IngestionRun[]>("/api/ingestion-runs?limit=100");
  const rejected = runs.ok ? runs.data.filter((r) => r.status === "rejected") : [];
  return (
    <>
      <PageHeader
        title="Evaluation Lab"
        purpose="Measure every model against a baseline, inspect real failures, and block releases that regress."
      />
      <Section title="Model benchmarks">
        <Notice kind="planned" title="Arrives in Phase 4">
          Versioned gold sets, per-layout extraction scores, false-match rates, and regression gates in CI.
        </Notice>
      </Section>
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
