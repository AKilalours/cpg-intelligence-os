import Link from "next/link";

type Rate = { k: number; n: number; rate: number | null; ci95: [number, number] | null };
type Block = {
  regions: string[]; claims: number; actions: Record<string, number>;
  false_approvals: Rate; false_disputes: Rate; disputes_exact_to_the_cent: Rate;
  recoverable_disputed_correctly: string; true_recoverable_total: string;
  requests: { n: number; claim_was_valid: number; claim_was_invalid: number };
  engine: { false_approvals: Rate; false_disputes: Rate; needs_evidence: number };
};
export type AgentReport = { report_id: number; created_at: string; body: { agent: string; report_region: Block } };

// The agent beside the engine it builds on, on the regions it was never tuned on, scored once.
export function AgentProof({ r }: { r: AgentReport }) {
  const b = r.body.report_region;
  const rows: [string, Rate, Rate, boolean][] = [
    ["Valid claims disputed", b.false_disputes, b.engine.false_disputes, true],
    ["Invalid claims approved", b.false_approvals, b.engine.false_approvals, true],
  ];
  return (
    <section aria-label="Investigation agent" className="mb-14 rounded-3xl border border-rule bg-sheet p-6 md:p-10">
      <div className="mb-6 flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-3xl">The investigation agent, scored once</h2>
        <span className="text-sm text-muted">{r.body.agent} · {b.regions.join(" + ")} · {b.claims} claims · report {r.report_id}</span>
      </div>
      <div className="grid gap-6 md:grid-cols-2">
        {rows.map(([label, agent, engine]) => (
          <div key={label}>
            <div className="text-sm text-muted">{label}</div>
            <div className="mt-2 flex items-end gap-6">
              <div><div className="font-serif text-6xl font-light tabular-nums text-forest">{agent.k}</div><div className="text-sm">agent, of {agent.n}</div></div>
              <div className="pb-1"><div className="font-serif text-3xl font-light tabular-nums text-muted">{engine.k}</div><div className="text-xs text-muted">engine alone, of {engine.n}</div></div>
            </div>
          </div>
        ))}
      </div>
      <div className="mt-6 grid gap-3 sm:grid-cols-3">
        <div className="rounded-xl bg-paper p-4"><div className="font-serif text-3xl tabular-nums">{b.disputes_exact_to_the_cent.k} of {b.disputes_exact_to_the_cent.n}</div><div className="text-sm text-muted">disputes exact to the cent</div></div>
        <div className="rounded-xl bg-paper p-4"><div className="font-serif text-3xl tabular-nums">${Number(b.recoverable_disputed_correctly).toLocaleString("en-US", { minimumFractionDigits: 2 })}</div><div className="text-sm text-muted">disputed correctly, of ${Number(b.true_recoverable_total).toLocaleString("en-US", { minimumFractionDigits: 2 })} recoverable</div></div>
        <div className="rounded-xl bg-paper p-4"><div className="font-serif text-3xl tabular-nums">{b.requests.n}</div><div className="text-sm text-muted">document requests ({b.requests.claim_was_valid} for claims that were valid); the engine left {b.engine.needs_evidence} undecided</div></div>
      </div>
      <p className="mt-4 text-sm text-muted">
        It reads only what a supplier has, enforced by the database. A shelf-price drop can stop a dispute, never approve a claim.{" "}
        <Link href="/investigation" className="underline decoration-rule underline-offset-2 hover:decoration-ink">See the cases</Link>
      </p>
    </section>
  );
}
