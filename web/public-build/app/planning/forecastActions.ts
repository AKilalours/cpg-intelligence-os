import { snap } from "@/lib/snapshot";
import type { ForecastBrief, ForecastSeries } from "@/lib/types";

const LIMIT = 100; // must match the API cap (le=100) and LIST_LIMIT in scripts/public_snapshot.py

// The live API searches all 30,490 series; the public copy filters the recorded top 100 of each sort order.
export async function findSeries(q: string, store: string, cls: string, sort: string): Promise<ForecastBrief[]> {
  const list = (await snap<ForecastBrief[]>(`/api/forecast/find?sort=${sort || "dollars"}&limit=${LIMIT}`)) ?? [];
  const needle = q.trim().toUpperCase();
  return list.filter((b) => (!needle || b.item.includes(needle)) && (!store || b.store === store) && (!cls || b.class === cls)).slice(0, 12);
}

export async function getSeries(item: string, store: string): Promise<ForecastSeries | null> {
  return snap<ForecastSeries>(`/api/forecast/series?${new URLSearchParams({ item, store })}`);
}
