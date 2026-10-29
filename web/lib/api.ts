import "server-only";

const BASE = process.env.CPGOS_API_URL ?? "http://127.0.0.1:8100";

export type ApiResult<T> = { ok: true; data: T } | { ok: false; error: string };

// Server-side fetch with no caching: every page view shows the ledger as it is now.
export async function api<T>(path: string): Promise<ApiResult<T>> {
  try {
    const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
    if (!res.ok) {
      const body = await res.text();
      return { ok: false, error: `API returned ${res.status} for ${path}: ${body.slice(0, 200)}` };
    }
    return { ok: true, data: (await res.json()) as T };
  } catch {
    return {
      ok: false,
      error: `Can't reach the API at ${BASE}. Start it with: make api`,
    };
  }
}
