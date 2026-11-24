"use server";

import { api } from "@/lib/api";
import type { ForecastSeries, LostRow, LostWeek } from "@/lib/types";

export async function loadCell(store: string, week: string): Promise<LostRow[]> {
  const r = await api<LostRow[]>(`/api/lost/cell?store=${encodeURIComponent(store)}&week=${encodeURIComponent(week)}`);
  return r.ok ? r.data : [];
}

export async function loadItem(item: string, store: string): Promise<{ s: ForecastSeries | null; flags: LostWeek[] }> {
  const q = `item=${encodeURIComponent(item)}&store=${encodeURIComponent(store)}`;
  const [s, f] = await Promise.all([api<ForecastSeries>(`/api/forecast/series?${q}`), api<LostWeek[]>(`/api/lost/item?${q}`)]);
  return { s: s.ok ? s.data : null, flags: f.ok ? f.data : [] };
}
