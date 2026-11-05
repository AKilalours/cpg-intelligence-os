import { api } from "@/lib/api";
import type { CaseRow } from "@/lib/types";
import { Notice, PageHeader, Cmd } from "@/components/ui";
import { CaseQueue } from "./CaseQueue";

export const dynamic = "force-dynamic";

export default async function InvestigationPage() {
  const res = await api<CaseRow[]>("/api/cases");
  return (
    <>
      <PageHeader title="Investigate" purpose="The agent reads every claim. You decide." />
      {!res.ok ? <Notice kind="error" title="Cases unavailable">{res.error}</Notice>
        : res.data.length === 0 ? (
          <Notice kind="empty" title="No investigations yet">Run the agent: <Cmd>make agent-score</Cmd></Notice>
        ) : <CaseQueue rows={res.data} />}
    </>
  );
}
