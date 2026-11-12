"use server";

import { api } from "@/lib/api";
import type { ForecastBrief, ForecastSeries } from "@/lib/types";

export async function findSeries(q: string, store: string, cls: string, sort: string): Promise<ForecastBrief[]> {
  const p = new URLSearchParams({ q, store, cls, sort, limit: "12" });
  const r = await api<ForecastBrief[]>(`/api/forecast/find?${p}`);
  return r.ok ? r.data : [];
}

export async function getSeries(item: string, store: string): Promise<ForecastSeries | null> {
  const p = new URLSearchParams({ item, store });
  const r = await api<ForecastSeries>(`/api/forecast/series?${p}`);
  return r.ok ? r.data : null;
}
