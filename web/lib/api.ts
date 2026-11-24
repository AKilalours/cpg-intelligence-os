import "server-only";

const BASE = process.env.CPGOS_API_URL ?? "http://127.0.0.1:8100";

// status: the HTTP status, or 0 when the API could not be reached at all. Pages need it to tell
// "nothing published yet" (404) from "the API is down" (0) or "the API broke" (500).
export type ApiResult<T> = { ok: true; data: T } | { ok: false; error: string; status: number };

// Server-side fetch with no caching: every page view shows the ledger as it is now.
export async function api<T>(path: string): Promise<ApiResult<T>> {
  try {
    const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
    if (!res.ok) {
      const body = await res.text();
      let detail = "";
      try { detail = (JSON.parse(body) as { detail?: string }).detail ?? ""; } catch { /* not JSON */ }
      return { ok: false, status: res.status, error: detail || `API returned ${res.status} for ${path}: ${body.slice(0, 200)}` };
    }
    return { ok: true, data: (await res.json()) as T };
  } catch {
    return {
      ok: false,
      status: 0,
      error: `Can't reach the API at ${BASE}. Start it with: make api`,
    };
  }
}

// Server-side POST for human reviews. The API records them as the database's reviewer role.
export async function apiPost<T>(path: string, body: unknown): Promise<ApiResult<T>> {
  try {
    const res = await fetch(`${BASE}${path}`, {
      method: "POST",
      cache: "no-store",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text();
      let detail = text.slice(0, 300);
      try {
        detail = (JSON.parse(text) as { detail?: string }).detail ?? detail;
      } catch {
        // not JSON: keep the text
      }
      return { ok: false, status: res.status, error: detail };
    }
    return { ok: true, data: (await res.json()) as T };
  } catch {
    return { ok: false, status: 0, error: `Can't reach the API at ${BASE}. Start it with: make api` };
  }
}

export function apiBase(): string {
  return BASE;
}
