import Link from "next/link";
import { api } from "@/lib/api";
import { count, money, pct } from "@/lib/format";
import type { CashAppItem, CashAppSummary, Deduction, Overview } from "@/lib/types";
import { Cmd, Figure, Notice, PageHeader, Section, Status } from "@/components/ui";

export const dynamic = "force-dynamic";

function deadline(days: number | null) {
  if (days === null) return <span className="text-muted">Unknown</span>;
  if (days < 0) return <span className="font-semibold text-debit">closed {-days} days ago</span>;
  if (days <= 21) return <span className="bg-tag px-1">{days} days left</span>;
  return <span>{days} days left</span>;
}

const VERDICT: Record<string, string> = {
  valid: "Valid",
  invalid: "Not valid",
  partially_valid: "Over-claimed",
  needs_evidence: "Needs evidence",
};

function Verdict({ v }: { v: Deduction["verdict"] }) {
  if (!v) return <span className="text-muted">Not assessed</span>;
  const tone = v === "valid" ? "" : v === "needs_evidence" ? "text-muted" : "font-semibold text-debit";
  return <span className={tone}>{VERDICT[v]}</span>;
}

function why(e: Record<string, unknown> | null): string {
  if (!e) return "";
  return Object.entries(e)
    .filter(([k]) => k !== "engine")
    .map(([k, v]) => `${k.replace(/_/g, " ")}: ${String(v)}`)
    .join("; ");
}

export default async function FinancePage() {
  const [overview, cash, queue] = await Promise.all([
    api<Overview>("/api/overview"),
    api<CashAppSummary>("/api/finance/cash-application?limit=25"),
    api<Deduction[]>("/api/deductions?order=priority&limit=1000"),
  ]);
  const asOf = overview.ok ? overview.data.exposure.as_of : null;
  return (
    <>
      <PageHeader
        title="Finance"
        purpose="Match payments to invoices, check every deduction against its evidence, and dispute the ones that aren't valid before the window closes."
      />
      {asOf ? (
        <p className="-mt-6 mb-8 text-sm text-muted">
          Ledger date {asOf}. Deadlines count from this date, the last day of the financial world.
        </p>
      ) : null}
      <Section title="Cash application" note="Posted automatically only at 90% confidence or higher">
        {!cash.ok ? (
          <Notice kind="error" title="Cash application unavailable">{cash.error}</Notice>
        ) : cash.data.cash_lines === 0 ? (
          <Notice kind="empty" title="No payments yet">
            Build the world and reconcile it: <Cmd>make finance-world</Cmd> then <Cmd>make reconcile</Cmd>.
          </Notice>
        ) : (
          <CashApp data={cash.data} />
        )}
      </Section>
      <Section title="Deduction queue" note="Engine order: urgent and recoverable first">
        {!queue.ok ? (
          <Notice kind="error" title="Deductions unavailable">{queue.error}</Notice>
        ) : queue.data.length === 0 ? (
          <Notice kind="empty" title="No deductions loaded">
            Run <Cmd>make finance-world</Cmd> then <Cmd>make reconcile</Cmd>.
          </Notice>
        ) : (
          <DeductionQueue rows={queue.data} />
        )}
      </Section>
    </>
  );
}

function CashApp({ data: c }: { data: CashAppSummary }) {
  return (
    <>
      <div className="mb-6 flex flex-wrap divide-x divide-rule border-y border-rule">
        <Figure value={pct(c.auto_applied / c.cash_lines)} label={`Posted automatically (${count(c.auto_applied)} of ${count(c.cash_lines)} lines)`} />
        <Figure value={count(c.in_review)} label="Suggested match awaiting a person" due={c.in_review > 0} />
        <Figure value={count(c.unmatched)} label="No safe suggestion" />
        <Figure value={money(c.unapplied_cash)} label="Cash not yet posted to an invoice" />
      </div>
      {c.review_queue.length > 0 ? <CashTable title="Review queue, largest first" rows={c.review_queue} suggested /> : null}
      {c.unmatched_lines.length > 0 ? <CashTable title="Unmatched cash" rows={c.unmatched_lines} /> : null}
    </>
  );
}

function CashTable({ title, rows, suggested = false }: { title: string; rows: CashAppItem[]; suggested?: boolean }) {
  return (
    <div className="mb-6 overflow-x-auto">
      <h3 className="mb-2 text-sm font-semibold">{title}</h3>
      <table className="ledger bg-sheet">
        <thead>
          <tr>
            <th>Check</th><th>Paid</th><th>Reference on remittance</th><th className="num">Amount</th>
            {suggested ? <><th>Suggested invoice</th><th className="num">Confidence</th><th>Why</th></> : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.remittance_line_id}>
              <td className="whitespace-nowrap">{r.payment_reference}</td>
              <td className="whitespace-nowrap">{r.payment_date}</td>
              <td>{r.raw_invoice_ref ?? <span className="text-muted">none</span>}</td>
              <td className="num font-figure text-lg">{money(r.amount)}</td>
              {suggested ? (
                <>
                  <td>{r.suggested_invoice}</td>
                  <td className="num">{r.confidence === null ? "" : pct(Number(r.confidence), 0)}</td>
                  <td className="max-w-md text-sm text-muted">{why(r.explanation)}</td>
                </>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DeductionQueue({ rows }: { rows: Deduction[] }) {
  const toDispute = rows.filter((d) => d.verdict && d.verdict !== "valid");
  const recoverable = toDispute.reduce((cents, d) => cents + toCents(d.recoverable_amount), 0n);
  return (
    <>
      <div className="mb-6 flex flex-wrap divide-x divide-rule border-y border-rule">
        <Figure value={count(rows.length)} label="Deductions taken" />
        <Figure value={count(toDispute.length)} label="Not fully supported by evidence" due={toDispute.length > 0} />
        <Figure value={money(fromCents(recoverable))} label="Recoverable if disputed" />
      </div>
      <div className="overflow-x-auto">
        <table className="ledger bg-sheet">
          <thead>
            <tr>
              <th className="num">#</th><th>Claim</th><th>Code</th><th>Reason</th><th>Store</th>
              <th className="num">Taken</th><th>Verdict</th><th className="num">Recoverable</th><th>Dispute window</th><th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((d) => (
              <tr key={d.deduction_id}>
                <td className="num text-muted">{d.priority_rank ?? ""}</td>
                <td className="whitespace-nowrap"><Link className="underline decoration-rule underline-offset-2 hover:decoration-ink" href={`/finance/deductions/${d.deduction_id}`}>{d.claim_number}</Link></td>
                <td>{d.code ?? <span className="text-muted">Unmapped</span>}</td>
                <td>{d.raw_reason ?? ""}</td>
                <td>{d.store ?? ""}</td>
                <td className="num font-figure text-lg">{money(d.amount)}</td>
                <td><Verdict v={d.verdict} /></td>
                <td className="num font-figure text-lg">{d.recoverable_amount === null ? "" : money(d.recoverable_amount)}</td>
                <td className="whitespace-nowrap">{deadline(d.days_to_deadline)}</td>
                <td><Status value={d.status} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

// Sum money as integer cents: no float ever touches an amount.
function toCents(dec: string | null): bigint {
  if (!dec) return 0n;
  const neg = dec.startsWith("-");
  const [i, f = ""] = (neg ? dec.slice(1) : dec).split(".");
  const c = BigInt(i || "0") * 100n + BigInt((f + "00").slice(0, 2));
  return neg ? -c : c;
}

function fromCents(c: bigint): string {
  const neg = c < 0n;
  const a = neg ? -c : c;
  return `${neg ? "-" : ""}${a / 100n}.${String(a % 100n).padStart(2, "0")}`;
}
