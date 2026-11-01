"use client";

import { useActionState } from "react";
import { submitReview, type ReviewState } from "./actions";

// Approve the machine's answer, or correct it. Leaving the value empty on a correction means
// "this field is not on the document".
export function ReviewForm({ documentId, field, machineValue }: { documentId: number; field: string; machineValue: string | null }) {
  const [state, action, pending] = useActionState<ReviewState, FormData>(submitReview, null);
  return (
    <form action={action} className="flex flex-wrap items-end gap-2 text-sm">
      <input type="hidden" name="document_id" value={documentId} />
      <input type="hidden" name="field" value={field} />
      <label className="flex flex-col">
        <span className="text-xs text-muted">Reviewer</span>
        <input name="reviewer" required className="w-28 border border-rule bg-paper px-2 py-1" autoComplete="name" />
      </label>
      <label className="flex flex-col">
        <span className="text-xs text-muted">Correct value</span>
        <input name="value" defaultValue={machineValue ?? ""} className="w-40 border border-rule bg-paper px-2 py-1"
               placeholder="empty = not on page" />
      </label>
      <label className="flex flex-col">
        <span className="text-xs text-muted">Note</span>
        <input name="note" className="w-40 border border-rule bg-paper px-2 py-1" />
      </label>
      <button name="action" value="approve" disabled={pending}
              className="border border-ink px-3 py-1 hover:bg-ink hover:text-paper disabled:opacity-50">
        Approve
      </button>
      <button name="action" value="correct" disabled={pending}
              className="border border-ink bg-ink px-3 py-1 text-paper disabled:opacity-50">
        Correct
      </button>
      {state ? (
        <p role={state.ok ? "status" : "alert"} className={"w-full " + (state.ok ? "text-muted" : "text-debit")}>
          {state.message}
        </p>
      ) : null}
    </form>
  );
}
