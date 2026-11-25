import { snap } from "@/lib/snapshot";
import type { ForecastSeries, LostRow, LostWeek } from "@/lib/types";

export async function loadCell(store: string, week: string): Promise<LostRow[]> {
  return (await snap<LostRow[]>(`/api/lost/cell?store=${encodeURIComponent(store)}&week=${encodeURIComponent(week)}`)) ?? [];
}

export async function loadItem(item: string, store: string): Promise<{ s: ForecastSeries | null; flags: LostWeek[] }> {
  const q = `item=${encodeURIComponent(item)}&store=${encodeURIComponent(store)}`;
  const [s, f] = await Promise.all([snap<ForecastSeries>(`/api/forecast/series?${q}`), snap<LostWeek[]>(`/api/lost/item?${q}`)]);
  return { s, flags: f ?? [] };
}
