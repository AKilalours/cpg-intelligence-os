"use server";

import { api } from "@/lib/api";
import type { StpReplay } from "@/lib/types";

export async function runReplay(fault: number, audit: number, seed: number): Promise<StpReplay | null> {
  const r = await api<StpReplay>(`/api/stp/replay?fault=${fault}&audit=${audit}&seed=${seed}`);
  return r.ok ? r.data : null;
}
