import { api } from "@/lib/api";
import { NextSteps } from "@/components/Journey";
import { Notice, PageHeader } from "@/components/ui";
import { failureNotice } from "@/lib/result";
import type { ForecastBrief, ForecastSeries, PromoEvidence } from "@/lib/types";
import { PromoPlanner } from "./PromoPlanner";

export const dynamic = "force-dynamic";

export default async function PromotionsPage() {
  const ev = await api<PromoEvidence>("/api/promo/evidence?dept=FOODS_3");
  const items = await api<ForecastBrief[]>("/api/forecast/find?q=FOODS_3&limit=8");
  const first = items.ok && items.data[0]
    ? await api<ForecastSeries>(`/api/forecast/series?item=${encodeURIComponent(items.data[0].item)}&store=${encodeURIComponent(items.data[0].store)}`)
    : null;
  return (
    <>
      <PageHeader title="Promotions" purpose="Will a price cut pay back? The arithmetic, and what 23,080 real cuts did." />
      {!ev.ok ? (() => { const n = failureNotice(ev, "No promotion evidence yet", "Run make promo-publish, then refresh.");
        return <Notice kind={n.kind} title={n.title}>{n.body}</Notice>; })() :
        <PromoPlanner initial={ev.data} items={items.ok ? items.data : []} series={first && first.ok ? first.data : null} />}
      <NextSteps steps={[
        { href: "/planning", title: "Forecast the next 8 weeks", detail: "LightGBM against classical baselines", roles: ["planning"] },
        { href: "/lost-sales", title: "Check the item for empty-shelf weeks", roles: ["planning"] },
        { href: "/impact", title: "Size the opportunity for a brand", roles: ["leadership"] },
      ]} />
    </>
  );
}
