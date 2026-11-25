import { api } from "@/lib/api";
import { NextSteps } from "@/components/Journey";
import { Notice, PageHeader } from "@/components/ui";
import type { LostSummary } from "@/lib/types";
import { failureNotice } from "@/lib/result";
import { LostSales } from "./LostSales";

export const dynamic = "force-dynamic";

export default async function LostSalesPage() {
  const s = await api<LostSummary>("/api/lost/summary");
  return (
    <>
      <PageHeader title="Lost sales" purpose="Weeks an item was on the shelf and sold far less than it should have." />
      {s.ok ? <LostSales s={s.data} /> : (() => { const n = failureNotice(s, "No lost-sales flags yet", "Run python scripts/anomaly.py publish, then refresh.");
        return <Notice kind={n.kind} title={n.title}>{n.body}</Notice>; })()}
      <NextSteps steps={[
        { href: "/planning", title: "Forecast demand for these items", detail: "8 weeks ahead, with an 80% range", roles: ["planning"] },
        { href: "/promotions", title: "Would a price cut pay back?", roles: ["planning"] },
        { href: "/impact", title: "Size the opportunity for a brand", roles: ["leadership"] },
      ]} />
    </>
  );
}
