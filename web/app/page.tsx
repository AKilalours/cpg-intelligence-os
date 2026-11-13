import Link from "next/link";
import { api } from "@/lib/api";
import { count, when } from "@/lib/format";
import type { DriftStatus, EvaluationReport, MoneyFlow as Flow, Overview, ProofCard, ReviewQueueItem, StpStatus } from "@/lib/types";
import { ProofWall } from "./ProofWall";
import { GateReplay } from "./GateReplay";
import { DriftAlarms } from "./DriftAlarms";
import { Autonomy, type Fresh } from "./Autonomy";
import { MoneyFlow } from "./MoneyFlow";
import { CountUp } from "@/components/CountUp";
import { Cmd, Notice, Status } from "@/components/ui";

export const dynamic = "force-dynamic";


export default async function TodayPage() {
  const [ov, queue, flow, gate, sim, drift, proof] = await Promise.all([
    api<Overview>("/api/overview"),
    api<ReviewQueueItem[]>("/api/documents/queue?limit=500"),
    api<Flow>("/api/overview/flow"),
    api<StpStatus>("/api/stp"),
    api<EvaluationReport | null>("/api/evaluation/latest?kind=stp_fresh"),
    api<DriftStatus>("/api/drift"),
    api<ProofCard[]>("/api/proof"),
  ]);
  if (!ov.ok) return <Notice kind="error" title="Can't load today's numbers">{ov.error}</Notice>;
  const e = ov.data.exposure;
  const c = ov.data.coverage;
  const waiting = queue.ok ? queue.data.length : 0;

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
          {drift.ok ? <div className="mb-3"><DriftAlarms s={drift.data} /></div> : null}
          <div className="mb-3"><GateReplay /></div>
          <Autonomy lanes={gate.data.lanes}
                    fresh={sim.ok && sim.data ? (sim.data.body as unknown as Fresh) : null} />
        </section>
      ) : null}

      {proof.ok && proof.data.length ? (
        <section className="mt-12" aria-label="Proof">
          <h2 className="mb-4 text-3xl">Scored once, on data the system never saw</h2>
          <ProofWall cards={proof.data} />
        </section>
      ) : null}

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
