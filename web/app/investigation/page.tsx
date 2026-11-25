import { api } from "@/lib/api";
import { NextSteps } from "@/components/Journey";
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
      <NextSteps steps={[
        { href: "/finance", title: "Decide the cash and deductions queue", detail: "Approve, dispute or accept, under your name", roles: ["deductions"] },
        { href: "/evaluation", title: "How the agent was scored", detail: "1 wrongful dispute against the rules engine's 24", roles: ["leadership"] },
        { href: "/impact", title: "Size what disputes recover", roles: ["leadership"] },
      ]} />
    </>
  );
}
