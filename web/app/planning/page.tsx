import Link from "next/link";
import { api } from "@/lib/api";
import { count, quantity } from "@/lib/format";
import type { SalesSeries, SeriesKey } from "@/lib/types";
import { Notice, PageHeader, Section } from "@/components/ui";
import { SeriesExplorer } from "./SeriesExplorer";
import { ForecastExplorer } from "./ForecastExplorer";
import type { ForecastBrief, ForecastSeries, ForecastSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

type Search = { sku?: string; store?: string };

// Next.js 15+ passes URL parameters as a Promise.
export default async function PlanningPage({ searchParams }: { searchParams: Promise<Search> }) {
  const params = await searchParams;
  const keys = await api<SeriesKey[]>("/api/sales/series-keys?limit=25");
  const pick =
    params.sku && params.store
      ? { sku: params.sku, store: params.store }
      : keys.ok && keys.data[0] ? { sku: keys.data[0].sku_code, store: keys.data[0].store } : null;
  const series = pick
    ? await api<SalesSeries>(`/api/sales/series?sku_code=${encodeURIComponent(pick.sku)}&store=${encodeURIComponent(pick.store)}`)
    : null;

  const fsum = await api<ForecastSummary>("/api/forecast/summary");
  const flist = fsum.ok ? await api<ForecastBrief[]>("/api/forecast/find?limit=12") : null;
  const first = flist && flist.ok && flist.data[0]
    ? await api<ForecastSeries>(`/api/forecast/series?item=${encodeURIComponent(flist.data[0].item)}&store=${encodeURIComponent(flist.data[0].store)}`)
    : null;

  return (
    <>
      <PageHeader
        title="Planning"
        purpose="Real Walmart sales (M5), by store and item."
      />
      {fsum.ok ? (
        <section className="mb-12" aria-label="Demand forecasts">
          <h2 className="mb-4 text-3xl">Demand, 8 weeks ahead</h2>
          <ForecastExplorer summary={fsum.data} initialList={flist && flist.ok ? flist.data : []} initial={first && first.ok ? first.data : null} />
        </section>
      ) : (
        <Notice kind="empty" title="No forecasts published yet">Run make forecast-publish, then refresh.</Notice>
      )}
      {!keys.ok ? (
        <Notice kind="error" title="Sales data unavailable">{keys.error}</Notice>
      ) : keys.data.length === 0 ? (
        <Notice kind="empty" title="No sales loaded">
          Load the real M5 Walmart data to see series here.
        </Notice>
      ) : (
        <div className="grid gap-10 xl:grid-cols-[minmax(0,1fr)_18rem]">
          <Section title={pick ? `${pick.sku} at ${pick.store}` : "Series"}>
            {series && series.ok ? (
              <>
                <SeriesExplorer key={`${pick!.sku}-${pick!.store}`} series={series.data} />
                <p className="mt-3 text-xs text-muted">
                  {count(series.data.points.length)} {series.data.granularity === "week" ? "weeks" : "days"}, {series.data.data_origin.replace(/_/g, " ")}. Periods before the item was first priced are excluded.
                </p>
              </>
            ) : series ? (
              <Notice kind="error" title="Series unavailable">{series.error}</Notice>
            ) : null}
          </Section>
          <Section title="Highest-volume series">
            <table className="ledger bg-sheet">
              <thead><tr><th>SKU</th><th>Store</th><th className="num">Units</th></tr></thead>
              <tbody>
                {keys.data.map((k) => {
                  const active = pick?.sku === k.sku_code && pick?.store === k.store;
                  return (
                    <tr key={`${k.sku_code}-${k.store}`} className={active ? "bg-paper" : undefined}>
                      <td>
                        <Link aria-current={active ? "true" : undefined}
                              className="underline decoration-rule underline-offset-4 hover:decoration-ink"
                              href={`/planning?sku=${encodeURIComponent(k.sku_code)}&store=${encodeURIComponent(k.store)}`}>
                          {k.sku_code}
                        </Link>
                      </td>
                      <td>{k.store}</td>
                      <td className="num">{quantity(k.total_units)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Section>
        </div>
      )}
      <Section title="Forecasts and promotion analysis">
        <Notice kind="planned" title="Arrives in Phase 5">
          Seasonal-naive, Croston/TSB, ETS, and LightGBM, compared with rolling-origin backtests.
        </Notice>
      </Section>
    </>
  );
}
