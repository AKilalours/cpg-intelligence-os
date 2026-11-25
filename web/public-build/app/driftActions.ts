import { READ_ONLY } from "@/lib/snapshot";

export async function clearAlarm(_id: number, _reviewer: string, _note: string): Promise<{ ok: boolean; error?: string }> {
  return { ok: false, error: READ_ONLY };
}
