import Link from "next/link";
import { api } from "@/lib/api";
import { money, when } from "@/lib/format";
import type { DeductionDetail } from "@/lib/types";
import { Notice, PageHeader, Section, Status } from "@/components/ui";

export const dynamic = "force-dynamic";

// Evidence values are shown exactly as stored; nested records (e.g. the
// promotions a scan-back was checked against) render as their own rows.
function EvidenceRows({ evidence }: { evidence: Record<string, unknown> }) {
  return (
    <table className="ledger bg-sheet">
      <tbody>
        {Object.entries(evidence).map(([k, v]) => (
          <tr key={k}>
            <th className="w-56 text-left align-top">{k.replace(/_/g, " ")}</th>
            <td className="font-mono text-sm">
              {Array.isArray(v) && v.every((x) => typeof x === "object" && x !== null) ? (
                <ul className="space-y-1">
                  {v.map((x, i) => (
                    <li key={i}>
                      {Object.entries(x as Record<string, unknown>).map(([a, b]) => `${a.replace(/_/g, " ")} ${String(b)}`).join(" · ")}
                    </li>
                  ))}
                </ul>
              ) : Array.isArray(v) ? v.join(" to ") : String(v)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default async function DeductionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const res = await api<DeductionDetail>(`/api/deductions/${encodeURIComponent(id)}`);
  if (!res.ok) {
    return (
      <>
        <PageHeader title="Deduction" purpose="The evidence behind one verdict." />
        <Notice kind="error" title="Deduction unavailable">{res.error}</Notice>
      </>
    );
  }
  const d = res.data;
  return (
    <>
      <p className="mb-4 text-sm"><Link className="underline underline-offset-2" href="/finance">Back to the queue</Link></p>
      <PageHeader
        title={`Claim ${d.claim_number}`}
        purpose={`${d.partner} took ${money(d.amount)} on ${d.deduction_date} as ${d.code ?? "an unmapped code"}${d.raw_reason ? `, "${d.raw_reason}"` : ""}.`}
      />
      <Section title="Verdict">
        {!d.verdict ? (
          <Notice kind="empty" title="Not assessed yet">Run the validator with make reconcile.</Notice>
        ) : (
          <table className="ledger bg-sheet">
            <tbody>
              <tr><th className="w-56 text-left">Verdict</th><td>{d.verdict.replace(/_/g, " ")}</td></tr>
              <tr><th className="text-left">Deciding check</th><td className="font-mono text-sm">{d.rule}</td></tr>
              <tr><th className="text-left">Supported by evidence</th><td className="font-figure text-lg">{d.supported_amount === null ? "n/a" : money(d.supported_amount)}</td></tr>
              <tr><th className="text-left">Recoverable</th><td className="font-figure text-lg">{d.recoverable_amount === null ? "" : money(d.recoverable_amount)}</td></tr>
              <tr><th className="text-left">Dispute deadline</th><td>{d.dispute_deadline} ({d.days_to_deadline} days from the ledger date)</td></tr>
              <tr><th className="text-left">Status</th><td><Status value={d.status} /></td></tr>
              <tr><th className="text-left">Engine</th><td className="text-sm text-muted">{d.engine_version}, calculation #{d.calculation_id}, {d.assessed_at ? when(d.assessed_at) : ""}</td></tr>
            </tbody>
          </table>
        )}
      </Section>
      {d.evidence ? (
        <Section title="Evidence used" note="Every value the verdict depends on, from the ledger">
          <EvidenceRows evidence={d.evidence} />
        </Section>
      ) : null}
    </>
  );
}
