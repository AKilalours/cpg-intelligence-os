import { READ_ONLY } from "@/lib/snapshot";

export type ReviewState = { ok: boolean; message: string } | null;

export async function submitReview(_prev: ReviewState, _form: FormData): Promise<ReviewState> {
  return { ok: false, message: READ_ONLY };
}
