"use server";

import { revalidatePath } from "next/cache";
import { apiPost } from "@/lib/api";

export async function clearAlarm(id: number, reviewer: string, note: string): Promise<{ ok: boolean; error?: string }> {
  if (!reviewer.trim()) return { ok: false, error: "Enter your name first: clearing is attributed." };
  if (!note.trim()) return { ok: false, error: "Say what you checked: clearing lets the engine post on its own again." };
  const r = await apiPost<{ cleared_by: string }>(`/api/drift/${id}/clear`, { reviewer, note });
  if (!r.ok) return { ok: false, error: r.error };
  revalidatePath("/");
  return { ok: true };
}
