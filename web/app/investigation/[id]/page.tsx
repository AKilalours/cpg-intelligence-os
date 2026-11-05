import Link from "next/link";
import { api } from "@/lib/api";
import type { CaseDetail } from "@/lib/types";
import { Notice } from "@/components/ui";
import { CaseView } from "./CaseView";

export const dynamic = "force-dynamic";

export default async function CasePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const res = await api<CaseDetail>(`/api/cases/${encodeURIComponent(id)}`);
  return (
    <>
      <p className="mb-4 text-sm"><Link className="underline decoration-rule underline-offset-2 hover:decoration-ink" href="/investigation">All cases</Link></p>
      {res.ok ? <CaseView c={res.data} /> : <Notice kind="error" title="Case unavailable">{res.error}</Notice>}
    </>
  );
}
