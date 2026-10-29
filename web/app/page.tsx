import { api } from "@/lib/api";
import { count, money, when } from "@/lib/format";
import type { Overview } from "@/lib/types";
import { Cmd, Figure, Notice, PageHeader, Section, Status } from "@/components/ui";

export const dynamic = "force-dynamic";

export default async function OverviewPage() {
  const res = await api<Overview>("/api/overview");
  return (
    <>
      <PageHeader
        title="Overview"
        purpose="What needs attention today, and whether the data behind every number is trustworthy."
      />
      {!res.ok ? (
        <Notice kind="error" title="Overview unavailable">{res.error}</Notice>
      ) : (
        <Body data={res.data} />
      )}
    </>
  );
}

function Body({ data }: { data: Overview }) {
  const { exposure: e, coverage: c } = data;
  return (
    <>
      <Section title="What needs attention">
        <div className="flex flex-wrap divide-x divide-rule border-y border-rule">
          <Figure value={money(e.open_deduction_amount)} label={`Open deductions (${count(e.open_deductions)})`} />
          <Figure value={count(e.deductions_due_14d)} label="Disputes due in 14 days" due={e.deductions_due_14d > 0} />
          <Figure value={money(e.unapplied_cash)} label="Unapplied cash" />
          <Figure value={count(e.proposed_cash_applications)} label="Cash matches awaiting review" />
        </div>
        {e.open_deductions === 0 && e.proposed_cash_applications === 0 ? (
          <p className="mt-3 text-sm text-muted">
            No payments or deductions are loaded yet. They arrive with the reconciliation module.
          </p>
        ) : null}
      </Section>

      <Section title="Data behind the numbers" note={`${count(c.real_dataset_versions)} of ${count(c.dataset_versions)} dataset files are real public data`}>
        <table className="ledger max-w-3xl bg-sheet">
          <tbody>
            <Row label="Sales observations" value={count(c.sales_observations)} />
            <Row label="SKUs" value={count(c.skus)} />
            <Row label="Stores" value={count(c.stores)} />
            <Row label="Source documents" value={count(c.source_documents)} />
            <Row label="Rows held in quarantine" value={count(c.quarantined_rows)} />
          </tbody>
        </table>
      </Section>

      <Section title="Recent data loads">
        {data.recent_runs.length === 0 ? (
          <Notice kind="empty" title="No data loaded yet">
            Download a dataset, then load it: <Cmd>python scripts/download_data.py m5</Cmd>
          </Notice>
        ) : (
          <div className="overflow-x-auto">
            <table className="ledger bg-sheet">
              <thead>
                <tr>
                  <th>Dataset</th><th>File</th><th>Origin</th><th>Status</th>
                  <th className="num">Rows in</th><th className="num">Loaded</th>
                  <th className="num">Quarantined</th><th>Code</th><th>Started</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_runs.map((r) => (
                  <tr key={r.run_id}>
                    <td>{r.dataset_key}</td>
                    <td>{r.file_name}</td>
                    <td>{r.data_origin.replace(/_/g, " ")}</td>
                    <td>
                      <Status value={r.status} />
                      {r.failure_reason ? <div className="text-xs text-muted">{r.failure_reason}</div> : null}
                    </td>
                    <td className="num">{r.rows_in === null ? "" : count(r.rows_in)}</td>
                    <td className="num">{r.rows_loaded === null ? "" : count(r.rows_loaded)}</td>
                    <td className="num">{r.rows_quarantined === null ? "" : count(r.rows_quarantined)}</td>
                    <td>{r.code_version}</td>
                    <td>{when(r.started_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <tr>
      <td>{label}</td>
      <td className="num w-40">{value}</td>
    </tr>
  );
}
