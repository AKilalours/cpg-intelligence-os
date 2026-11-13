"use server";

import { api } from "@/lib/api";
import type { ForecastBrief, ForecastSeries, PromoEvidence } from "@/lib/types";

export async function loadEvidence(dept: string): Promise<PromoEvidence | null> {
  const r = await api<PromoEvidence>(`/api/promo/evidence?dept=${encodeURIComponent(dept)}`);
  return r.ok ? r.data : null;
}

export async function findItems(q: string, dept: string): Promise<ForecastBrief[]> {
  const r = await api<ForecastBrief[]>(`/api/forecast/find?q=${encodeURIComponent(q || dept)}&limit=8`);
  return r.ok ? r.data : [];
}

export async function loadSeries(item: string, store: string): Promise<ForecastSeries | null> {
  const r = await api<ForecastSeries>(`/api/forecast/series?item=${encodeURIComponent(item)}&store=${encodeURIComponent(store)}`);
  return r.ok ? r.data : null;
}
