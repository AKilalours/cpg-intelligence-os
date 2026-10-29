import { api } from "@/lib/api";
import { money } from "@/lib/format";
import type { Deduction } from "@/lib/types";
import { Notice, PageHeader, Section, Status } from "@/components/ui";

export const dynamic = "force-dynamic";

function deadline(days: number | null) {
  if (days === null) return <span className="text-muted">Unknown</span>;
  if (days < 0) return <span className="font-semibold text-debit">{-days} days overdue</span>;
  if (days <= 14) return <span className="bg-tag px-1">{days} days left</span>;
  return <span>{days} days left</span>;
}

export default async function FinancePage() {
  const res = await api<Deduction[]>("/api/deductions");
  return (
    <>
      <PageHeader
        title="Finance"
        purpose="Match payments to invoices, investigate deductions, and dispute the ones that aren't valid before the window closes."
      />
      <Section title="Deductions" note="Ordered by dispute deadline">
        {!res.ok ? (
          <Notice kind="error" title="Deductions unavailable">{res.error}</Notice>
        ) : res.data.length === 0 ? (
          <Notice kind="empty" title="No deductions loaded">
            Deductions are generated on real invoices and published KeHE rules in Phase 2, then listed here.
          </Notice>
        ) : (
          <div className="overflow-x-auto">
            <table className="ledger bg-sheet">
              <thead>
                <tr>
                  <th>Claim</th><th>Partner</th><th>Code</th><th>Type</th>
                  <th className="num">Amount</th><th>Taken</th><th>Dispute window</th><th>Status</th>
                </tr>
              </thead>
              <tbody>
                {res.data.map((d) => (
                  <tr key={d.deduction_id}>
                    <td>{d.claim_number}</td>
                    <td>{d.partner}</td>
                    <td>{d.code ?? <span className="text-muted">Unmapped</span>}</td>
                    <td>{d.code_family?.replace(/_/g, " ") ?? ""}</td>
                    <td className="num font-figure text-lg">{money(d.amount)}</td>
                    <td>{d.deduction_date}</td>
                    <td>{deadline(d.days_to_deadline)}</td>
                    <td><Status value={d.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
      <Section title="Cash application">
        <Notice kind="planned" title="Arrives in Phase 2">
          One-to-one, one-to-many, and partial-payment matching with a measured false-match rate.
        </Notice>
      </Section>
    </>
  );
}
