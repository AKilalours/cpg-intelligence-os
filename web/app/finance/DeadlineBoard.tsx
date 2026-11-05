"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState, useTransition } from "react";
import { count, fromCents, money, toCents } from "@/lib/format";
import type { Deduction, DeductionDetail } from "@/lib/types";
import { useReviewer } from "@/components/useReviewer";
import { decideDeduction, loadDeduction } from "./actions";

type V = NonNullable<Deduction["verdict"]>;
const LANES: { v: V; label: string; fill: string }[] = [
  { v: "invalid", label: "Not valid", fill: "#B3402F" },
  { v: "partially_valid", label: "Over-claimed", fill: "#D9822B" },
  { v: "needs_evidence", label: "Needs evidence", fill: "#9A9A9A" },
  { v: "valid", label: "Valid", fill: "#609D7B" },
];
const OPEN = new Set(["open", "investigating"]);
const lane = (v: Deduction["verdict"]) => LANES.find((l) => l.v === v) ?? LANES[2];

export function DeadlineBoard({ rows }: { rows: Deduction[] }) {
  const [decided, setDecided] = useState<Record<number, string>>({});
  const [filter, setFilter] = useState<V | "all">("all");
  const [openOnly, setOpenOnly] = useState(true);
  const [shown, setShown] = useState(10);
  const [sel, setSel] = useState<number | null>(null);
  const status = useCallback((d: Deduction) => decided[d.deduction_id] ?? d.status, [decided]);

  const list = useMemo(() => rows.filter((d) => (filter === "all" || d.verdict === filter) && (!openOnly || OPEN.has(status(d)))),
    [rows, filter, openOnly, status]);
  const recoverable = list.filter((d) => d.verdict && d.verdict !== "valid").reduce((c, d) => c + toCents(d.recoverable_amount), 0n);
  const days = rows.map((d) => d.days_to_deadline ?? 0);
  const lo = Math.min(-7, ...days), hi = Math.max(30, ...days);
  const x = (d: number) => 130 + ((d - lo) / (hi - lo)) * 830;
  const maxAmt = Math.max(...rows.map((d) => Number(d.amount)), 1);
  const idx = sel === null ? -1 : list.findIndex((d) => d.deduction_id === sel);

  useEffect(() => {
    document.body.dataset.drawer = sel === null ? "" : "open";
    if (sel === null) return;
    const k = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, textarea")) return;
      if (e.key === "Escape") setSel(null);
      else if (e.key === "j" && idx < list.length - 1) setSel(list[idx + 1].deduction_id);
      else if (e.key === "k" && idx > 0) setSel(list[idx - 1].deduction_id);
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [sel, idx, list]);

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Chip on={filter === "all"} onClick={() => setFilter("all")}>All {count(rows.length)}</Chip>
        {LANES.map((l) => (
          <Chip key={l.v} on={filter === l.v} onClick={() => setFilter(l.v)} dot={l.fill}>
            {l.label} {count(rows.filter((d) => d.verdict === l.v).length)}
          </Chip>
        ))}
        <label className="ml-2 flex items-center gap-2 text-sm text-muted">
          <input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} className="accent-forest" />
          Undecided only
        </label>
        <span className="ml-auto text-sm">
          <span className="font-serif text-2xl tabular-nums text-credit">{money(fromCents(recoverable))}</span>
          <span className="ml-2 text-muted">recoverable in view</span>
        </span>
      </div>

      <div className="overflow-x-auto rounded-2xl border border-rule bg-sheet p-3">
        <svg viewBox="0 0 980 250" className="min-w-[720px]" role="img" aria-label="Dispute deadlines by verdict">
          {LANES.map((l, n) => (
            <g key={l.v}>
              <line x1="130" x2="960" y1={40 + n * 52} y2={40 + n * 52} stroke="#E4E1D8" />
              <circle cx="12" cy={40 + n * 52} r="5" fill={l.fill} />
              <text x="24" y={40 + n * 52} dominantBaseline="central" className="fill-ink text-[12px]">{l.label}</text>
            </g>
          ))}
          <rect x={x(lo)} y="14" width={x(0) - x(lo)} height="200" fill="#B3402F" opacity="0.05" />
          <text x={x(lo) + 6} y="232" className="fill-debit text-[11px]">Window closed</text>
          <line x1={x(0)} x2={x(0)} y1="14" y2="214" stroke="#1F2A24" strokeDasharray="3 3" />
          <text x={x(0)} y="9" textAnchor="middle" className="fill-ink text-[11px] font-semibold">Ledger date</text>
          <line x1={x(21)} x2={x(21)} y1="14" y2="214" stroke="#C9E34F" strokeWidth="3" />
          <text x={x(21) + 4} y="232" className="fill-ink text-[11px]">21 days: act now</text>
          <text x="960" y="232" textAnchor="end" className="fill-muted text-[11px]">{hi} days out</text>
          {rows.map((d, n) => {
            const l = lane(d.verdict), li = LANES.indexOf(l);
            const inView = list.includes(d);
            const r = 3 + 7 * Math.sqrt(Number(d.amount) / maxAmt);
            const done = !OPEN.has(status(d));
            return (
              <circle key={d.deduction_id} cx={x(d.days_to_deadline ?? 0)} cy={40 + li * 52 + ((n * 7) % 32) - 16} r={r}
                      fill={done ? "#fff" : l.fill} stroke={sel === d.deduction_id ? "#1F2A24" : l.fill}
                      strokeWidth={sel === d.deduction_id ? 3 : 1.5} opacity={inView ? 0.85 : 0.12}
                      className="cursor-pointer transition-opacity hover:opacity-100 animate-land"
                      style={{ animationDelay: `${Math.min(n, 120) * 6}ms`, transformBox: "fill-box", transformOrigin: "center" }}
                      onClick={() => setSel(d.deduction_id)}>
                <title>{`${d.claim_number} · ${money(d.amount)} · ${d.days_to_deadline} days`}</title>
              </circle>
            );
          })}
        </svg>
      </div>

      <ul className="mt-4 divide-y divide-rule overflow-hidden rounded-2xl border border-rule bg-sheet">
        {list.slice(0, shown).map((d) => {
          const l = lane(d.verdict), dl = d.days_to_deadline ?? 0;
          return (
            <li key={d.deduction_id}>
              <button onClick={() => setSel(d.deduction_id)}
                      className={`grid w-full grid-cols-[auto_1fr_auto] items-center gap-4 px-4 py-3 text-left hover:bg-paper md:grid-cols-[3rem_10rem_1fr_9rem_12rem_7rem] ${sel === d.deduction_id ? "bg-paper" : ""}`}>
                <span className="text-sm text-muted tabular-nums">#{d.priority_rank}</span>
                <span className="font-semibold">{d.claim_number}<span className="block text-xs font-normal text-muted">{d.partner}</span></span>
                <span className="hidden truncate text-sm md:block">{d.code ?? "Unmapped"} · {d.raw_reason}</span>
                <span className="text-right font-serif text-xl tabular-nums">{money(d.amount)}</span>
                <span className="hidden md:block">
                  <span className="block h-1.5 rounded-full bg-rule">
                    <span className={`block h-1.5 origin-left rounded-full animate-grow ${dl < 0 ? "bg-debit" : dl <= 21 ? "bg-tag" : "bg-leaf"}`}
                          style={{ width: `${Math.max(4, Math.min(100, 100 - (dl / Math.max(hi, 1)) * 100))}%` }} />
                  </span>
                  <span className="text-xs text-muted">{dl < 0 ? `closed ${-dl}d ago` : `${dl} days left`}</span>
                </span>
                <span className="hidden text-right text-sm md:block">
                  {OPEN.has(status(d)) ? <span style={{ color: l.fill }} className="font-semibold">{l.label}</span>
                    : <span className="rounded-full bg-paper px-2 py-0.5">{status(d).replace("_", " ")}</span>}
                </span>
              </button>
            </li>
          );
        })}
        {list.length === 0 ? <li className="p-6 text-center text-muted">Nothing here. Change the filter.</li> : null}
      </ul>
      {list.length > shown ? (
        <button onClick={() => setShown((s) => s + 10)} className="mt-3 rounded-full border border-rule px-4 py-2 text-sm hover:border-forest">
          Show 10 more of {count(list.length - shown)}
        </button>
      ) : null}

      {sel !== null ? (
        <Drawer id={sel} row={rows.find((d) => d.deduction_id === sel)!} status={status(rows.find((d) => d.deduction_id === sel)!)}
                onClose={() => setSel(null)} position={`${idx + 1} of ${list.length}`}
                onDecided={(st) => setDecided((m) => ({ ...m, [sel]: st }))} />
      ) : null}
    </div>
  );
}

function Chip({ on, onClick, dot, children }: { on: boolean; onClick: () => void; dot?: string; children: React.ReactNode }) {
  return (
    <button onClick={onClick} aria-pressed={on}
            className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm transition ${on ? "border-forest bg-forest text-white" : "border-rule bg-sheet hover:border-forest"}`}>
      {dot ? <span className="h-2.5 w-2.5 rounded-full" style={{ background: dot }} /> : null}
      {children}
    </button>
  );
}

function Drawer({ id, row, status, position, onClose, onDecided }: {
  id: number; row: Deduction; status: string; position: string; onClose: () => void; onDecided: (s: string) => void;
}) {
  const [detail, setDetail] = useState<DeductionDetail | null>(null);
  const [reviewer, setReviewer] = useReviewer();
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [pending, start] = useTransition();
  useEffect(() => { setDetail(null); setMsg(null); loadDeduction(id).then(setDetail); }, [id]);

  const act = useCallback((action: "dispute" | "accept") => {
    if (pending || !OPEN.has(status)) return;
    start(async () => {
      const r = await decideDeduction(id, action, reviewer);
      if (!r.ok) { setMsg({ ok: false, text: r.error }); return; }
      onDecided(r.status);
      setMsg({ ok: true, text: r.agrees === false ? "Recorded. You overruled the engine; that is logged too." : "Recorded and audited." });
    });
  }, [id, pending, reviewer, status, onDecided]);

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, textarea")) return;
      if (e.key === "d") act("dispute");
      else if (e.key === "v") act("accept");
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [act]);

  const l = lane(row.verdict);
  const engineSays = row.verdict === "valid" ? "accept" : row.verdict === "needs_evidence" ? "look closer" : "dispute";
  const ev = detail?.evidence ? Object.entries(detail.evidence).slice(0, 8) : [];
  const units = detail?.evidence && "claimed_units" in detail.evidence
    ? (["claimed_units", "shipped_units", "received_units"] as const)
        .filter((k) => detail.evidence![k] !== undefined && detail.evidence![k] !== null)
        .map((k) => ({ k: k.replace("_units", ""), v: Number(detail.evidence![k]) }))
    : [];
  const umax = Math.max(1, ...units.map((u) => u.v));
  return (
    <>
    <div className="fixed inset-0 z-30 bg-ink/20 animate-rise" onClick={onClose} aria-hidden />
    <aside className="fixed inset-y-0 right-0 z-40 flex w-full max-w-md flex-col border-l border-rule bg-sheet shadow-2xl animate-rise" aria-label={`Claim ${row.claim_number}`}>
      <div className="flex items-center justify-between border-b border-rule px-6 py-4">
        <span className="text-sm text-muted">{position} · j / k to move</span>
        <button onClick={onClose} className="rounded-full px-3 py-1 text-sm hover:bg-paper">Close Esc</button>
      </div>
      <div className="flex-1 overflow-y-auto px-6 py-5">
        <p className="text-sm text-muted">{row.partner} · {row.code ?? "unmapped"} · {row.deduction_date}</p>
        <h3 className="mt-1 font-serif text-3xl">Claim {row.claim_number}</h3>
        <div className="mt-5 grid grid-cols-2 gap-3">
          <div className="rounded-xl bg-paper p-3"><p className="text-xs text-muted">Taken</p><p className="font-serif text-2xl tabular-nums">{money(row.amount)}</p></div>
          <div className="rounded-xl bg-paper p-3"><p className="text-xs text-muted">Recoverable</p><p className="font-serif text-2xl tabular-nums text-credit">{row.recoverable_amount ? money(row.recoverable_amount) : "$0.00"}</p></div>
        </div>
        <p className="mt-4 flex items-center gap-2">
          <span className="h-3 w-3 rounded-full" style={{ background: l.fill }} />
          <span className="font-semibold">{l.label}</span>
          <span className="text-sm text-muted">engine says {engineSays}</span>
        </p>
        {row.rule ? <p className="mt-1 text-sm text-muted">Rule: {row.rule.replace(/_/g, " ")}</p> : null}
        {units.length > 1 ? (
          <div className="mt-5 space-y-2">
            {units.map((u, n) => (
              <div key={u.k} className="grid grid-cols-[5rem_1fr_2.5rem] items-center gap-2 text-sm">
                <span className="capitalize text-muted">{u.k}</span>
                <span className="h-3 rounded-full bg-paper">
                  <span className={`block h-3 origin-left rounded-full animate-grow ${u.k === "claimed" ? "bg-debit/80" : "bg-leaf"}`}
                        style={{ width: `${(100 * u.v) / umax}%`, animationDelay: `${n * 150}ms` }} />
                </span>
                <span className="text-right font-semibold tabular-nums">{u.v}</span>
              </div>
            ))}
          </div>
        ) : null}
        <div className="mt-5">
          <p className="mb-2 text-sm font-semibold">Evidence</p>
          {detail === null ? <div className="h-24 animate-pulse rounded-xl bg-paper" /> : ev.length === 0 ? <p className="text-sm text-muted">None stored.</p> : (
            <dl className="space-y-1.5 text-sm">
              {ev.map(([k, v]) => (
                <div key={k} className="grid grid-cols-[9rem_1fr] gap-2 animate-rise">
                  <dt className="text-muted">{k.replace(/_/g, " ")}</dt>
                  <dd className="truncate font-mono text-xs leading-5">{Array.isArray(v) ? `${v.length} record${v.length === 1 ? "" : "s"}` : String(v)}</dd>
                </div>
              ))}
            </dl>
          )}
          <Link href={`/finance/deductions/${id}`} className="mt-3 inline-block text-sm underline decoration-rule underline-offset-2 hover:decoration-ink">All evidence</Link>
        </div>
      </div>
      <div className="border-t border-rule px-6 py-4">
        {OPEN.has(status) ? (
          <>
            <input value={reviewer} onChange={(e) => setReviewer(e.target.value)} placeholder="Your name"
                   className="mb-3 w-full rounded-full border border-rule px-4 py-2 focus:border-forest focus:outline-none" />
            <div className="flex gap-3">
              <button onClick={() => act("dispute")} disabled={pending}
                      className={`flex-1 rounded-full px-4 py-2.5 font-semibold disabled:opacity-50 ${engineSays === "dispute" ? "bg-forest text-white hover:bg-ink" : "border border-forest text-forest hover:bg-paper"}`}>
                Dispute <kbd className="ml-1 text-xs opacity-70">D</kbd>
              </button>
              <button onClick={() => act("accept")} disabled={pending}
                      className={`flex-1 rounded-full px-4 py-2.5 font-semibold disabled:opacity-50 ${engineSays === "accept" ? "bg-forest text-white hover:bg-ink" : "border border-forest text-forest hover:bg-paper"}`}>
                Accept as valid <kbd className="ml-1 text-xs opacity-70">V</kbd>
              </button>
            </div>
          </>
        ) : <p className="rounded-full bg-paper px-4 py-2 text-center font-semibold">Decided: {status.replace("_", " ")}</p>}
        {msg ? <p role="status" className={`mt-2 text-sm ${msg.ok ? "text-credit" : "font-semibold text-debit"}`}>{msg.text}</p> : null}
      </div>
    </aside>
    </>
  );
}
