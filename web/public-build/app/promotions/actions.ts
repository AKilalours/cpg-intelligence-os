import { snap } from "@/lib/snapshot";
import type { ForecastBrief, ForecastSeries, PromoEvidence } from "@/lib/types";

const LIST = 30; // must match PROMO_LIST in scripts/public_snapshot.py

export async function loadEvidence(dept: string): Promise<PromoEvidence | null> {
  return snap<PromoEvidence>(`/api/promo/evidence?dept=${encodeURIComponent(dept)}`);
}

export async function findItems(q: string, dept: string): Promise<ForecastBrief[]> {
  const list = (await snap<ForecastBrief[]>(`/api/forecast/find?q=${encodeURIComponent(dept)}&limit=${LIST}`)) ?? [];
  const needle = q.trim().toUpperCase();
  return list.filter((b) => !needle || b.item.includes(needle)).slice(0, 8);
}

export async function loadSeries(item: string, store: string): Promise<ForecastSeries | null> {
  return snap<ForecastSeries>(`/api/forecast/series?item=${encodeURIComponent(item)}&store=${encodeURIComponent(store)}`);
}
