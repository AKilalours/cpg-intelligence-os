import { READ_ONLY } from "@/lib/snapshot";

export type CaseResult = { ok: true; decision: string; case_status: string; deduction_status: string } | { ok: false; error: string };

export async function decideCase(_caseId: number, _action: string, _reviewer: string, _note: string): Promise<CaseResult> {
  return { ok: false, error: READ_ONLY };
}
