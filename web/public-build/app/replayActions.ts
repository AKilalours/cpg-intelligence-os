import { snap } from "@/lib/snapshot";
import type { StpReplay } from "@/lib/types";

// Recorded grid (scripts/public_snapshot.py); the sliders snap to the nearest recorded setting.
const FAULTS = [0, 0.05, 0.1, 0.2, 0.3], AUDITS = [0.02, 0.05, 0.1, 0.2, 0.33, 0.5], SEEDS = 3;
const near = (xs: number[], v: number) => xs.reduce((a, b) => (Math.abs(b - v) < Math.abs(a - v) ? b : a));

export async function runReplay(fault: number, audit: number, seed: number): Promise<StpReplay | null> {
  const s = ((Math.max(1, Math.round(seed)) - 1) % SEEDS) + 1;
  return snap<StpReplay>(`/api/stp/replay?fault=${near(FAULTS, fault)}&audit=${near(AUDITS, audit)}&seed=${s}`);
}
