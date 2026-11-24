import { api } from "@/lib/api";
import { Notice, PageHeader } from "@/components/ui";
import type { LostSummary } from "@/lib/types";
import { LostSales } from "./LostSales";

export const dynamic = "force-dynamic";

export default async function LostSalesPage() {
  const s = await api<LostSummary>("/api/lost/summary");
  return (
    <>
      <PageHeader title="Lost sales" purpose="Weeks an item was on the shelf and sold far less than it should have." />
      {s.ok ? <LostSales s={s.data} /> : <Notice kind="empty" title="No lost-sales flags yet">Run python scripts/anomaly.py publish, then refresh.</Notice>}
    </>
  );
}
