import Link from "next/link";
import { api } from "@/lib/api";
import type { DocumentView, FieldDecision } from "@/lib/types";
import { Notice, PageHeader, Section } from "@/components/ui";
import { ReviewForm } from "./ReviewForm";

export const dynamic = "force-dynamic";

const READER = { rules: "OCR + rules", local_model: "Local model", claude: "Claude", models: "Both models" } as Record<string, string>;

function Box({ f, n }: { f: FieldDecision; n: number }) {
  if (!f.bbox) return null;
  const [x0, y0, x1, y1] = f.bbox;
  const tone = f.review ? "border-ink" : f.status === "accepted" ? "border-credit" : "border-debit";
  return (
    <div
      className={`absolute border-2 ${tone}`}
      style={{ left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` }}
      title={`${f.field}: ${f.value ?? "no value"}`}
    >
      <span className="absolute -top-4 left-0 bg-paper px-0.5 text-[10px] leading-none">{n}</span>
    </div>
  );
}

export default async function DocumentReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const res = await api<DocumentView>(`/api/documents/${id}`);
  if (!res.ok) {
    return (
      <>
        <PageHeader title={`Document ${id}`} purpose="Review what the machines read." />
        <Notice kind="error" title="Document unavailable">{res.error}</Notice>
      </>
    );
  }
  const d = res.data;
  const shown = d.fields.filter((f) => f.status === "review" || f.value !== null || f.review);
  return (
    <>
      <PageHeader
        title={`Document ${d.source_document_id}`}
        purpose="Each field shows what the readers saw, what the rule decided and why. Approve or correct the fields waiting for a person; every action is attributed, audited, and kept."
      />
      <p className="mb-6 text-sm text-muted">
        <Link href="/documents" className="underline underline-offset-4">Back to the review queue</Link>
        {" · "}rule {d.policy_version ?? "none"} · {d.open_fields} field{d.open_fields === 1 ? "" : "s"} waiting
      </p>
      <div className="grid gap-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <Section title="Page" note="Green accepted, red waiting, black reviewed">
          <div className="relative border border-rule bg-sheet">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={`/documents/${d.source_document_id}/image`} alt={`Document ${d.source_document_id}`} className="block w-full" />
            {shown.map((f, i) => <Box key={f.field} f={f} n={i + 1} />)}
          </div>
        </Section>
        <Section title="Fields">
          <ol className="divide-y divide-rule border-y border-rule">
            {shown.map((f, i) => (
              <li key={f.field} className="py-3">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <p>
                    <span className="mr-2 text-xs text-muted">{i + 1}</span>
                    <span className="font-semibold">{f.field.replace(/_/g, " ")}</span>{" "}
                    <span className="font-figure tabular-nums">{f.value ?? (f.status === "accepted" ? "not on page" : "")}</span>
                  </p>
                  <span className={f.review ? "text-sm" : f.status === "accepted" ? "text-sm text-credit" : "text-sm font-semibold text-debit"}>
                    {f.review ? `${f.review.action === "approve" ? "Approved" : "Corrected"} by ${f.review.reviewer}` : f.status === "accepted" ? "Accepted" : "Waiting"}
                  </span>
                </div>
                <p className="text-sm text-muted">{f.reason}</p>
                {Object.keys(f.candidates).length ? (
                  <p className="text-sm text-muted">
                    {Object.entries(f.candidates).map(([k, v]) => `${READER[k] ?? k}: ${v}`).join(" · ")}
                  </p>
                ) : null}
                {f.review ? (
                  <p className="text-sm">Reviewed value: {f.review.value ?? "not on this document"}</p>
                ) : f.status === "review" ? (
                  <div className="mt-2"><ReviewForm documentId={d.source_document_id} field={f.field} machineValue={f.value} /></div>
                ) : null}
              </li>
            ))}
          </ol>
        </Section>
      </div>
    </>
  );
}
