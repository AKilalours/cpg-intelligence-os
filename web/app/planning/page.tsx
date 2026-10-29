import Link from "next/link";
import { api } from "@/lib/api";
import { count, quantity } from "@/lib/format";
import type { SalesSeries, SeriesKey } from "@/lib/types";
import { Notice, PageHeader, Section } from "@/components/ui";
import { Sparkline } from "@/components/Sparkline";

export const dynamic = "force-dynamic";

type Search = { sku?: string; store?: string };

export default async function PlanningPage({ searchParams }: { searchParams: Search }) {
  const keys = await api<SeriesKey[]>("/api/sales/series-keys?limit=25");
  const pick =
    searchParams.sku && searchParams.store
      ? { sku: searchParams.sku, store: searchParams.store }
      : keys.ok && keys.data[0] ? { sku: keys.data[0].sku_code, store: keys.data[0].store } : null;
  const series = pick
    ? await api<SalesSeries>(`/api/sales/series?sku_code=${encodeURIComponent(pick.sku)}&store=${encodeURIComponent(pick.store)}`)
    : null;

  return (
    <>
      <PageHeader
        title="Planning"
        purpose="Forecast demand for every SKU at every store, and judge promotions against what would have sold without them."
      />
      {!keys.ok ? (
        <Notice kind="error" title="Sales data unavailable">{keys.error}</Notice>
      ) : keys.data.length === 0 ? (
        <Notice kind="empty" title="No sales loaded">
          Load the real M5 Walmart data to see series here.
        </Notice>
      ) : (
        <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <Section title={pick ? `${pick.sku} at ${pick.store}` : "Series"}>
            {series && series.ok ? (
              <>
                <Sparkline values={series.data.points.map((p) => p.units)} />
                <p className="mt-2 text-sm text-muted">
                  {`${count(series.data.points.length)} days of daily units, data origin: ${series.data.data_origin.replace(/_/g, " ")}. `}
                  Days before the item was first priced are excluded.
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
