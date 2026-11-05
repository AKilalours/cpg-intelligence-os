"use server";

import { revalidatePath } from "next/cache";
import { apiPost } from "@/lib/api";

export type CaseResult = { ok: true; decision: string; case_status: string; deduction_status: string } | { ok: false; error: string };

export async function decideCase(caseId: number, action: string, reviewer: string, note: string): Promise<CaseResult> {
  if (!reviewer.trim()) return { ok: false, error: "Enter your name first: every decision is attributed." };
  const r = await apiPost<{ decision: string; case_status: string; deduction_status: string }>(
    `/api/cases/${caseId}/decision`, { action, reviewer, note: note || null });
  if (!r.ok) return { ok: false, error: r.error };
  revalidatePath(`/investigation/${caseId}`);
  revalidatePath("/investigation");
  revalidatePath("/finance");
  revalidatePath("/");
  return { ok: true, ...r.data };
}
