"use client";

import { useMemo, useState, useTransition } from "react";
import type { ForecastSeries, LostRow, LostSummary, LostWeek } from "@/lib/types";
import { loadCell, loadItem } from "./actions";

const RED = "#B3402F", FOREST = "#28543E", INK = "#1F2A24", RULE = "#E4E1D8";
const money = (x: number) => (x >= 1e6 ? `$${(x / 1e6).toFixed(1)}M` : x >= 1e3 ? `$${Math.round(x / 1e3).toLocaleString()}K` : `$${Math.round(x).toLocaleString()}`);
const pct = (x: number) => `${Math.round(x * 100)}%`;

function Grid({ s, pick, onPick }: { s: LostSummary; pick: [number, number] | null; onPick: (r: number, c: number) => void }) {
  const [hover, setHover] = useState<[number, number] | null>(null);
  // Colour by rank, not raw dollars, so a handful of huge weeks does not wash the rest out.
  const sorted = useMemo(() => s.grid.flat().filter((v) => v > 0).sort((a, b) => a - b), [s]);
  const shade = (v: number) => { if (v <= 0) return RULE; const r = sorted.findIndex((x) => x >= v) / Math.max(1, sorted.length - 1);
    return `rgba(179,64,47,${0.15 + 0.85 * r})`; };
  const hv = hover ?? pick;
  return (
    <div>
      <div className="overflow-x-auto">
        <div className="inline-grid min-w-full gap-[2px]" style={{ gridTemplateColumns: `3.5rem repeat(${s.weeks.length}, minmax(9px, 1fr))` }}>
          {s.stores.map((st, r) => (
            <div key={st} className="contents">
              <div className="pr-2 text-right text-xs font-bold leading-[18px]">{st}</div>
              {s.weeks.map((wk, c) => {
                const on = pick && pick[0] === r && pick[1] === c;
                return <button key={wk} onClick={() => onPick(r, c)} onMouseEnter={() => setHover([r, c])} onMouseLeave={() => setHover(null)}
                               aria-label={`${st}, week of ${wk}: ${money(s.grid[r][c])} estimated lost`}
                               className={`h-[18px] rounded-[3px] ${on ? "ring-2 ring-ink" : ""}`} style={{ background: shade(s.grid[r][c]) }} />;
              })}
            </div>
          ))}
        </div>
      </div>
      <div className="mt-2 flex justify-between text-xs text-muted"><span>{s.weeks[0]}</span>
        <span className="font-bold text-ink">{hv ? `${s.stores[hv[0]]}, week of ${s.weeks[hv[1]]}: ${money(s.grid[hv[0]][hv[1]])}` : "tap a week"}</span>
        <span>{s.weeks[s.weeks.length - 1]}</span></div>
    </div>
  );
}

function ItemChart({ s, flags, mark }: { s: ForecastSeries; flags: LostWeek[]; mark?: string }) {
  const weeks = [...s.weeks];
  const sold = [...s.history, ...s.actual];
  const flagged = new Map(flags.map((f) => [f.week, f]));
  const max = Math.max(1, ...sold.map((v) => v ?? 0), ...flags.map((f) => f.expected));
  const W = 760, H = 240, L = 44, B = 32, FS = 18, bw = (W - L - 8) / weeks.length;
  const y = (v: number) => 10 + (1 - v / max) * (H - 10 - B);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label={`Weekly sales for ${s.item} at ${s.store}, flagged weeks in red`}>
      <text x={L - 8} y={y(max) + 6} fontSize={FS} textAnchor="end" fill="#6C6C6C">{Math.round(max)}</text>
      <text x={L - 8} y={y(0)} fontSize={FS} textAnchor="end" fill="#6C6C6C">0</text>
      {weeks.map((wk, i) => {
        const v = sold[i], f = flagged.get(wk), x = L + i * bw;
        return (
          <g key={wk}>
            {wk === mark ? <rect x={x - 1} y={8} width={bw + 2} height={H - B - 6} fill="none" stroke={INK} strokeWidth="2" rx="3" /> : null}
            {f ? <rect x={x + 1} y={y(f.expected)} width={bw - 2} height={y(v ?? 0) - y(f.expected)} fill={RED} opacity="0.25" stroke={RED} strokeDasharray="3 2" /> : null}
            {v !== null && v !== undefined ? <rect x={x + 1} y={y(v)} width={bw - 2} height={y(0) - y(v)} fill={f ? RED : "#9A9A9A"} rx="1" /> : null}
            {f ? <title>{`${wk}: sold ${f.sold}, expected ${f.expected}, about $${f.lost.toFixed(0)} lost`}</title> : null}
          </g>
        );
      })}
      <text x={L} y={H - 8} fontSize={FS} fill="#6C6C6C">{weeks[0]?.slice(0, 7)}</text>
      <text x={W - 8} y={H - 8} fontSize={FS} textAnchor="end" fill="#6C6C6C">{weeks[weeks.length - 1]?.slice(0, 7)}</text>
    </svg>
  );
}

export function LostSales({ s }: { s: LostSummary }) {
  const [pick, setPick] = useState<[number, number] | null>(null);
  const [rows, setRows] = useState<LostRow[]>([]);
  const [item, setItem] = useState<{ s: ForecastSeries | null; flags: LostWeek[]; name: string; mark?: string } | null>(null);
  const [pending, start] = useTransition();
  const openCell = (r: number, c: number) => { setPick([r, c]); start(async () => setRows(await loadCell(s.stores[r], s.weeks[c]))); };
  const openItem = (it: string, st: string) => start(async () => { const x = await loadItem(it, st); setItem({ ...x, name: `${it} at ${st}`, mark: pick ? s.weeks[pick[1]] : undefined }); });
  const deptMax = Math.max(...Object.values(s.by_dept));
  const t = s.test;
  return (
    <div className="space-y-6">
      <div className="grid gap-3 md:grid-cols-[1.4fr_1fr_1fr]">
        <div className="rounded-2xl p-5 text-paper" style={{ background: RED }}>
          <div className="text-sm opacity-80">Estimated sales lost in 52 weeks</div>
          <div className="font-serif text-6xl font-light">{money(s.lost)}</div>
          <div className="mt-1 text-sm opacity-80">{s.sold_total ? `${(100 * s.lost / s.sold_total).toFixed(1)}% of ${money(s.sold_total)} sold · ` : ""}{s.flagged_weeks.toLocaleString()} item-weeks</div>
        </div>
        {t ? (<>
          <div className="rounded-2xl bg-forest p-5 text-paper"><div className="text-sm opacity-80">Test stock-outs detected, by value</div>
            <div className="font-serif text-5xl font-light">{pct(t.caught_dollars)}</div><div className="mt-1 text-sm opacity-80">on held-out weeks</div></div>
          <div className="rounded-2xl border border-rule bg-sheet p-5"><div className="text-sm text-muted">Caught, by how fast an item sells</div>
            <div className="mt-3 space-y-2">{Object.entries(t.by_speed).map(([k, v]) => (
              <div key={k} className="grid grid-cols-[6rem_1fr_2.5rem] items-center gap-2 text-sm"><span>{k}</span>
                <span className="h-3 rounded-full bg-paper"><span className="block h-full rounded-full bg-forest" style={{ width: `${(v.caught ?? 0) * 100}%` }} /></span>
                <span className="text-right tabular-nums">{pct(v.caught ?? 0)}</span></div>))}</div>
            <div className="mt-2 text-xs text-muted">A zero week is normal for a slow seller, so weekly data cannot flag it.</div></div>
        </>) : null}
      </div>

      <div className="rounded-2xl border border-rule bg-sheet p-5"><Grid s={s} pick={pick} onPick={openCell} /></div>

      <div className={`grid gap-4 lg:grid-cols-[22rem_minmax(0,1fr)] ${pending ? "opacity-60" : ""}`}>
        <div className="space-y-2">
          {(pick ? rows.map((r) => ({ item: r.item, store: s.stores[pick[0]], lost: r.lost, note: `sold ${Math.round(r.sold)}, usually ${Math.round(r.expected)}` })).slice(0, 12)
                 : s.top.slice(0, 12).map((r) => ({ item: r.item, store: r.store, lost: r.lost, note: `${r.weeks} flagged weeks` }))).map((r) => (
            <button key={r.item + r.store} onClick={() => openItem(r.item, r.store)}
                    className="grid w-full grid-cols-[1fr_auto] items-center gap-2 rounded-xl bg-sheet px-3 py-2 text-left text-sm hover:bg-rule">
              <span><b>{r.item}</b> <span className="text-muted">{r.store} · {r.note}</span></span>
              <span className="font-bold tabular-nums" style={{ color: RED }}>{money(r.lost)}</span>
            </button>))}
          {pick && rows.length > 12 ? <div className="text-xs text-muted">and {rows.length - 12} smaller ones that week</div> : null}
          {!pick ? <div className="pt-3 text-xs text-muted">Biggest losses over the year. Tap a week in the grid to see that week&apos;s items.</div> : null}
          <div className="pt-3 space-y-1">{Object.entries(s.by_dept).map(([d, v]) => (
            <div key={d} className="grid grid-cols-[6.5rem_1fr_3.5rem] items-center gap-2 text-xs"><span>{d.replace("_", " ")}</span>
              <span className="h-2.5 rounded-full bg-paper"><span className="block h-full rounded-full" style={{ width: `${(v / deptMax) * 100}%`, background: RED }} /></span>
              <span className="text-right tabular-nums">{money(v)}</span></div>))}</div>
        </div>
        <div className="min-w-0 self-start rounded-2xl border border-rule bg-sheet p-5 lg:sticky lg:top-20">
          {item?.s ? (<>
            <div className="text-xl font-bold">{item.name}</div>
            <ItemChart s={item.s} flags={item.flags} mark={item.mark} />
            <div className="mt-1 text-xs text-muted">Red bars: flagged weeks; the pale box above is what it usually sells; the outlined week is the one you tapped. Hover a red bar for the numbers.</div>
          </>) : <p className="text-muted">Tap an item to see its weeks.</p>}
        </div>
      </div>
      <p className="text-xs text-muted">An estimate: it assumes the item would have sold its usual amount; some flags are data problems rather than empty shelves.</p>
    </div>
  );
}
