// The public copy of the app is a static export: the same pages and components, reading answers the real API
// gave (recorded by scripts/public_snapshot.py) instead of a live API. Nothing here runs in the local app.

export const PUBLIC = process.env.NEXT_PUBLIC_CPGOS_PUBLIC === "1";
export const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
export const READ_ONLY =
  "This public copy is read-only, so decisions are not recorded here. In a running installation this button records the decision under your name.";

// Must match key() in scripts/public_snapshot.py.
export const snapKey = (path: string) => path.replace(/[^A-Za-z0-9]+/g, "_");

export async function snap<T>(path: string): Promise<T | null> {
  try {
    const r = await fetch(`${BASE}/snap/${snapKey(path)}.json`);
    return r.ok ? ((await r.json()) as T) : null;
  } catch {
    return null;
  }
}

export const docImage = (id: number | string) => (PUBLIC ? `${BASE}/snap/img/${id}.jpg` : `/documents/${id}/image`);
