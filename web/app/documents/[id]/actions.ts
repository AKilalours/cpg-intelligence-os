"use server";

import { revalidatePath } from "next/cache";
import { apiPost } from "@/lib/api";

export type ReviewState = { ok: boolean; message: string } | null;

// One form per field. The reviewer's name is required: every action is attributed and audited.
export async function submitReview(_prev: ReviewState, form: FormData): Promise<ReviewState> {
  const id = String(form.get("document_id") ?? "");
  const field = String(form.get("field") ?? "");
  const action = String(form.get("action") ?? "");
  const reviewer = String(form.get("reviewer") ?? "").trim();
  const raw = String(form.get("value") ?? "").trim();
  const note = String(form.get("note") ?? "").trim();
  if (!/^\d+$/.test(id) || !field) return { ok: false, message: "Missing document or field." };
  if (!reviewer) return { ok: false, message: "Enter your name: every review is attributed." };
  const res = await apiPost<{ reviewed_value: string | null; remembered: boolean }>(
    `/api/documents/${id}/fields/${encodeURIComponent(field)}/review`,
    { action, reviewer, value: action === "correct" && raw ? raw : null, note: note || null },
  );
  if (!res.ok) return { ok: false, message: res.error };
  revalidatePath(`/documents/${id}`);
  revalidatePath("/documents");
  const v = res.data.reviewed_value ?? "not on this document";
  return {
    ok: true,
    message: res.data.remembered ? `Corrected to ${v}; remembered as a regression case.` : `Recorded: ${v}.`,
  };
}
