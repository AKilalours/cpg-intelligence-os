import { READ_ONLY, snap } from "@/lib/snapshot";
import type { DeductionDetail } from "@/lib/types";

export type Result = { ok: true; status: string; agrees?: boolean | null } | { ok: false; error: string };

export async function decideCash(_lineId: number, _action: "approve" | "reject", _reviewer: string): Promise<Result> {
  return { ok: false, error: READ_ONLY };
}

export async function decideDeduction(_id: number, _action: "dispute" | "accept", _reviewer: string): Promise<Result> {
  return { ok: false, error: READ_ONLY };
}

export async function loadDeduction(id: number): Promise<DeductionDetail | null> {
  return snap<DeductionDetail>(`/api/deductions/${id}`);
}
