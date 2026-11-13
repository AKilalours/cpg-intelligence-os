import Link from "next/link";
import { api } from "@/lib/api";
import type { CaseDetail, CasePicture as Pic } from "@/lib/types";
import { CasePicture } from "./CasePicture";
import { Notice } from "@/components/ui";
import { CaseView } from "./CaseView";

export const dynamic = "force-dynamic";

export default async function CasePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [res, pic] = await Promise.all([api<CaseDetail>(`/api/cases/${encodeURIComponent(id)}`),
                                        api<Pic>(`/api/cases/${encodeURIComponent(id)}/picture`)]);
  return (
    <>
      <p className="mb-4 text-sm"><Link className="underline decoration-rule underline-offset-2 hover:decoration-ink" href="/investigation">All cases</Link></p>
      {res.ok && pic.ok ? <CasePicture p={pic.data} action={res.data.agent_action} reason={res.data.agent_reason} asOf={res.data.as_of} /> : null}
      {res.ok ? <CaseView c={res.data} /> : <Notice kind="error" title="Case unavailable">{res.error}</Notice>}
    </>
  );
}
