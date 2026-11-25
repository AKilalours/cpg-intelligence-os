// The visitor's journey: which workspace they chose, what they looked at last, and what to do next.
// Kept in the visitor's own browser only (localStorage); every read and write tolerates storage being blocked.

export type RoleId = "deductions" | "planning" | "leadership";

export interface Role { id: RoleId; title: string; who: string; start: string; first: string }

export const ROLES: Role[] = [
  { id: "deductions", title: "Deductions and cash", who: "Collections and AR analysts", start: "/finance", first: "Clear the disputes that are about to expire" },
  { id: "planning", title: "Demand planning", who: "Demand and supply planners", start: "/lost-sales", first: "Find where empty shelves cost the most" },
  { id: "leadership", title: "Finance leadership", who: "Controllers and finance leads", start: "/", first: "See where every retailer dollar went" },
];

export interface Visit { href: string; label: string; kind: string; at: number }

const ROLE_KEY = "cpgos.role", RECENT_KEY = "cpgos.recent";

function read<T>(key: string, fallback: T): T {
  try { const v = window.localStorage.getItem(key); return v ? (JSON.parse(v) as T) : fallback; } catch { return fallback; }
}
function write(key: string, value: unknown) {
  try { window.localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage blocked: the app still works */ }
}

export const getRole = (): Role | null => ROLES.find((r) => r.id === read<string | null>(ROLE_KEY, null)) ?? null;
export const setRole = (id: RoleId) => { write(ROLE_KEY, id); window.dispatchEvent(new Event("cpgos:journey")); };

// Most recent first, one entry per destination, at most eight.
export function addVisit(list: Visit[], v: Visit, max = 8): Visit[] {
  return [v, ...list.filter((x) => x.href !== v.href)].slice(0, max);
}
export const getRecent = (): Visit[] => read<Visit[]>(RECENT_KEY, []);
export const track = (v: Omit<Visit, "at">) => {
  write(RECENT_KEY, addVisit(getRecent(), { ...v, at: Date.now() }));
  window.dispatchEvent(new Event("cpgos:journey"));
};

// An item and store carried between pages in the address (?item=...&store=...).
export function focusFrom(search: string): { item: string; store: string } | null {
  const p = new URLSearchParams(search);
  const item = p.get("item"), store = p.get("store");
  return item && store && /^[A-Z0-9_]{1,40}$/.test(item) && /^[A-Z]{2}_\d{1,2}$/.test(store) ? { item, store } : null;
}
export const focusQuery = (item: string, store: string) => `?item=${encodeURIComponent(item)}&store=${encodeURIComponent(store)}`;
