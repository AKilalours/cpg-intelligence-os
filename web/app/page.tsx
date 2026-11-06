import Link from "next/link";
import { api } from "@/lib/api";
import { count, when } from "@/lib/format";
import type { EvaluationReport, MoneyFlow as Flow, Overview, ReviewQueueItem, StpStatus } from "@/lib/types";
import { Autonomy } from "./Autonomy";
import { MoneyFlow } from "./MoneyFlow";
import { CountUp } from "@/components/CountUp";
import { Cmd, Notice, Status } from "@/components/ui";

export const dynamic = "force-dynamic";

type Rate = { k: number; n: number; rate: number | null };

export default async function TodayPage() {
  const [ov, queue, inv, rec, ded, flow, gate, sim] = await Promise.all([
    api<Overview>("/api/overview"),
    api<ReviewQueueItem[]>("/api/documents/queue?limit=500"),
    api<EvaluationReport | null>("/api/evaluation/latest?kind=routing_holdout"),
    api<EvaluationReport | null>("/api/evaluation/latest?kind=cord_receipts_test"),
    api<EvaluationReport | null>("/api/evaluation/latest?kind=phase2a"),
    api<Flow>("/api/overview/flow"),
    api<StpStatus>("/api/stp"),
    api<EvaluationReport | null>("/api/evaluation/latest?kind=stp"),
  ]);
  if (!ov.ok) return <Notice kind="error" title="Can't load today's numbers">{ov.error}</Notice>;
  const e = ov.data.exposure;
  const c = ov.data.coverage;
  const waiting = queue.ok ? queue.data.length : 0;

  // Proof: the latest one-time scores, read from stored reports (never typed in by hand).
  const invAcc = inv.ok && inv.data ? (inv.data.body as { with_third_reader?: { accepted_correct?: Rate } }).with_third_reader?.accepted_correct : undefined;
  const recAcc = rec.ok && rec.data ? Object.values((rec.data.body as { policies?: Record<string, { accepted_amounts_exact: Rate }> }).policies ?? {})[0]?.accepted_amounts_exact : undefined;
  const dedBad = ded.ok && ded.data ? (ded.data.body as { deductions?: { report_region?: { false_approval: Rate } } }).deductions?.report_region?.false_approval : undefined;

  return (
    <>
      <h1 className="mb-8 font-serif text-5xl font-light tracking-tight md:text-6xl">Today, as of {e.as_of}</h1>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Tile href="/finance" hot={e.deductions_due_14d > 0} label="disputes due in 14 days">
          <CountUp to={e.deductions_due_14d} format="int" />
        </Tile>
        <Tile href="/documents" hot={waiting > 0} label="invoices need you">
          <CountUp to={waiting} format="int" />
        </Tile>
        <Tile href="/finance" label="cash matches to review">
          <CountUp to={e.proposed_cash_applications} format="int" />
        </Tile>
        <Tile href="/finance" label={`open deductions (${count(e.open_deductions)})`}>
          <CountUp to={Number(e.open_deduction_amount)} format="money" />
        </Tile>
      </div>

      {flow.ok && flow.data.cash.length + flow.data.deductions.length > 0 ? (
        <section className="mt-10" aria-label="Where the money went">
          <h2 className="mb-4 text-3xl">Where the retailers&apos; money went</h2>
          <MoneyFlow flow={flow.data} />
        </section>
      ) : null}

      {gate.ok && gate.data.lanes.length ? (
        <section className="mt-12" aria-label="Earned autonomy">
          <h2 className="mb-4 text-3xl">Posting on its own, only where it has earned it</h2>
          <Autonomy lanes={gate.data.lanes}
                    faults={sim.ok && sim.data ? (Object.values((sim.data.body as { worlds?: Record<string, { silent_fault?: Record<string, never> }> }).worlds ?? {})[0]?.silent_fault ?? null) : null} />
        </section>
      ) : null}

      <section className="mt-12" aria-label="Proof">
        <h2 className="mb-4 text-3xl">Scored once, on data the system never saw</h2>
        <div className="grid gap-3 md:grid-cols-3">
          <Proof rate={invAcc} label="invoice values right when accepted" href="/evaluation" />
          <Proof rate={recAcc} label="receipt photos right when accepted" href="/evaluation" />
          <Proof rate={dedBad} label="bad deduction claims approved" href="/evaluation" count />
        </div>
      </section>

      <p className="mt-12 text-sm text-muted">
        {count(c.sales_observations)} real sales rows · {count(c.source_documents)} documents · {count(c.skus)} SKUs · {count(c.stores)} stores · {count(c.quarantined_rows)} rows quarantined
      </p>

      <details className="mt-4 rounded-xl border border-rule bg-sheet p-4">
        <summary className="cursor-pointer font-bold">Data loads</summary>
        {ov.data.recent_runs.length === 0 ? (
          <p className="mt-3 text-sm">Nothing loaded yet: <Cmd>make download DATASET=m5</Cmd></p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="ledger">
              <thead><tr><th>Dataset</th><th>Status</th><th className="num">Rows loaded</th><th className="num">Quarantined</th><th>Started</th></tr></thead>
              <tbody>
                {ov.data.recent_runs.map((r) => (
                  <tr key={r.run_id}>
                    <td>{r.dataset_key}{r.scope.batch ? ` ${r.scope.batch.part}/${r.scope.batch.of}` : ""}</td>
                    <td><Status value={r.status} /></td>
                    <td className="num">{r.rows_loaded === null ? "" : count(r.rows_loaded)}</td>
                    <td className="num">{r.rows_quarantined === null ? "" : count(r.rows_quarantined)}</td>
                    <td>{when(r.started_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </details>
    </>
  );
}

function Tile({ href, label, hot = false, children }: { href: string; label: string; hot?: boolean; children: React.ReactNode }) {
  return (
    <Link href={href}
          className={"group rounded-2xl border p-5 transition hover:-translate-y-0.5 hover:shadow-[0_8px_24px_rgba(31,42,36,0.10)] " +
            (hot ? "border-ink bg-tag" : "border-rule bg-sheet hover:border-forest")}>
      <div className="font-serif text-5xl font-light leading-none">{children}</div>
      <div className="mt-3 text-sm">{label}</div>
    </Link>
  );
}

function Proof({ rate, label, href, count: asCount = false }: { rate?: Rate; label: string; href: string; count?: boolean }) {
  return (
    <Link href={href} className="rounded-2xl bg-forest p-5 text-paper transition hover:bg-ink">
      <div className="font-serif text-5xl font-light leading-none">
        {!rate ? "–" : asCount ? `${rate.k} of ${rate.n}` : <CountUp to={(rate.rate ?? 0) * 100} format="pct" />}
      </div>
      <div className="mt-3 text-sm text-paper/80">{label}</div>
      {rate && !asCount ? <div className="mt-1 text-xs text-paper/60">{count(rate.k)} of {count(rate.n)}</div> : null}
    </Link>
  );
}
