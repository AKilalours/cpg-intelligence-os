"use server";

import { revalidatePath } from "next/cache";
import { api, apiPost } from "@/lib/api";
import type { DeductionDetail } from "@/lib/types";

export type Result = { ok: true; status: string; agrees?: boolean | null } | { ok: false; error: string };

// Every decision is attributed: the API refuses an empty reviewer, and so do we, before the call.
export async function decideCash(lineId: number, action: "approve" | "reject", reviewer: string): Promise<Result> {
  if (!reviewer.trim()) return { ok: false, error: "Enter your name first: every decision is attributed." };
  const r = await apiPost<{ status: string }>(`/api/cash/${lineId}/review`, { action, reviewer });
  if (!r.ok) return { ok: false, error: r.error };
  revalidatePath("/finance");
  revalidatePath("/");
  return { ok: true, status: r.data.status };
}

export async function decideDeduction(id: number, action: "dispute" | "accept", reviewer: string): Promise<Result> {
  if (!reviewer.trim()) return { ok: false, error: "Enter your name first: every decision is attributed." };
  const r = await apiPost<{ status: string; agrees_with_engine: boolean | null }>(
    `/api/deductions/${id}/review`, { action, reviewer });
  if (!r.ok) return { ok: false, error: r.error };
  revalidatePath("/finance");
  revalidatePath("/");
  return { ok: true, status: r.data.status, agrees: r.data.agrees_with_engine };
}

export async function loadDeduction(id: number): Promise<DeductionDetail | null> {
  const r = await api<DeductionDetail>(`/api/deductions/${id}`);
  return r.ok ? r.data : null;
}
