import { api } from "@/lib/api";
import { count, money } from "@/lib/format";
import type { CashAppSummary, Deduction, Overview } from "@/lib/types";
import { Cmd, Notice, PageHeader } from "@/components/ui";
import { DeadlineBoard } from "./DeadlineBoard";
import { MatchDeck } from "./MatchDeck";

export const dynamic = "force-dynamic";

function Split({ c }: { c: CashAppSummary }) {
  const parts = [
    { n: c.auto_applied, label: "Posted by the engine", cls: "bg-leaf" },
    { n: c.approved_by_person ?? 0, label: "Approved by a person", cls: "bg-forest" },
    { n: c.in_review, label: "Waiting for you", cls: "bg-tag" },
    { n: c.unmatched, label: "No safe match", cls: "bg-debit/70" },
  ];
  return (
    <div className="mb-6">
      <div className="flex h-4 overflow-hidden rounded-full bg-rule">
        {parts.map((p, i) => p.n > 0 ? (
          <div key={p.label} className={`${p.cls} origin-left animate-grow`} title={`${p.label}: ${p.n}`}
               style={{ width: `${(100 * p.n) / c.cash_lines}%`, animationDelay: `${i * 180}ms` }} />
        ) : null)}
      </div>
      <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-sm">
        {parts.map((p) => (
          <span key={p.label} className="flex items-center gap-2">
            <span className={`h-2.5 w-2.5 rounded-full ${p.cls}`} />
            <span className="font-semibold tabular-nums">{count(p.n)}</span>
            <span className="text-muted">{p.label}</span>
          </span>
        ))}
        <span className="ml-auto text-muted">Unposted cash <span className="font-semibold text-ink">{money(c.unapplied_cash)}</span></span>
      </div>
    </div>
  );
}

export default async function FinancePage() {
  const [overview, cash, queue] = await Promise.all([
    api<Overview>("/api/overview"),
    api<CashAppSummary>("/api/finance/cash-application?limit=100"),
    api<Deduction[]>("/api/deductions?order=priority&limit=1000"),
  ]);
  const asOf = overview.ok ? overview.data.exposure.as_of : null;
  const empty = (
    <Notice kind="empty" title="Nothing loaded yet">
      Run <Cmd>make finance-world</Cmd> then <Cmd>make reconcile</Cmd>.
    </Notice>
  );
  return (
    <>
      <PageHeader title="Cash & deductions" />
      {asOf ? <p className="-mt-6 mb-8 text-sm text-muted">Ledger date {asOf}</p> : null}

      <section className="mb-14">
        <h2 className="mb-4 font-serif text-2xl">Match the cash</h2>
        {!cash.ok ? <Notice kind="error" title="Cash application unavailable">{cash.error}</Notice>
          : cash.data.cash_lines === 0 ? empty : (
            <>
              <Split c={cash.data} />
              <MatchDeck items={cash.data.review_queue} total={cash.data.in_review} />
            </>
          )}
      </section>

      <section>
        <h2 className="mb-4 font-serif text-2xl">Dispute before the window closes</h2>
        {!queue.ok ? <Notice kind="error" title="Deductions unavailable">{queue.error}</Notice>
          : queue.data.length === 0 ? empty : <DeadlineBoard rows={queue.data} />}
      </section>
    </>
  );
}
