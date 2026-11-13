"use client";

import { useMemo, useState, useTransition } from "react";
import type { ForecastBrief, ForecastSeries, PromoEvidence } from "@/lib/types";
import { findItems, loadEvidence, loadSeries } from "./actions";

const GREEN = "#609D7B", RED = "#B3402F", INK = "#1F2A24", FOREST = "#28543E";
const pct = (x: number, d = 0) => `${(x * 100).toFixed(d)}%`;
const signed = (x: number) => `${x >= 0 ? "+" : ""}${Math.round(x * 100)}%`;
const money = (x: number) => `${x < 0 ? "-" : ""}$${Math.abs(Math.round(x)).toLocaleString()}`;
// Units needed to keep profit flat when the price drops by d at a margin m (of the regular price).
const breakEven = (d: number, m: number) => (m > d ? d / (m - d) : Infinity);
// Floating point: 0.2 / (0.3 - 0.2) is 2.0000000000000004, so a lift of exactly 2.0 read as "short of
// break-even" while the profit change showed $0. Compare with a tolerance everywhere a verdict is drawn.
const pays = (lift: number, d: number, m: number) => lift >= breakEven(d, m) - 1e-9;

function Slider({ label, value, set, min, max, step, show }: { label: string; value: number; set: (v: number) => void; min: number; max: number; step: number; show: string }) {
  return (
    <label className="block rounded-2xl border border-rule bg-sheet p-4">
      <div className="flex items-baseline justify-between"><span className="text-sm text-muted">{label}</span>
        <span className="font-serif text-4xl font-light tabular-nums">{show}</span></div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => set(Number(e.target.value))}
             className="mt-3 w-full accent-[#28543E]" aria-label={label} />
    </label>
  );
}

function Scatter({ ev, depth, margin, onPick }: { ev: PromoEvidence; depth: number; margin: number; onPick: (i: number) => void }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 760, H = 420, L = 64, R = 44, T = 18, B = 48, FS = 18;
  const x = (d: number) => L + ((Math.min(d, 0.5) - 0.1) / 0.4) * (W - L - R);
  const y = (l: number) => T + (1 - (Math.max(-1, Math.min(l, 3)) + 1) / 4) * (H - T - B);
  // Show at most 2,500 points (stable sample) so the chart stays fast; the numbers use every cut.
  const idx = useMemo(() => { const n = ev.depth.length, step = Math.max(1, Math.ceil(n / 2500));
    return Array.from({ length: Math.ceil(n / step) }, (_, k) => k * step); }, [ev]);
  const curve = useMemo(() => { const pts: string[] = [];
    for (let d = 0.1; d <= 0.5001; d += 0.005) { const b = breakEven(d, margin); if (!isFinite(b) || b > 3) break; pts.push(`${x(d).toFixed(1)},${y(b).toFixed(1)}`); }
    return pts; }, [margin]);
  const lastX = curve.length ? Number(curve[curve.length - 1].split(",")[0]) : x(0.1);
  const h = hover === null ? null : hover;
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Each dot is a real price cut: how deep it was and how sales changed against stores that did not cut">
        <path d={`M${curve.join("L")}L${lastX},${T}L${x(0.1)},${T}Z`} fill={GREEN} opacity="0.10" />
        {lastX < x(0.5) ? <rect x={lastX} y={T} width={x(0.5) - lastX} height={H - T - B} fill={RED} opacity="0.06" /> : null}
        {[-1, 0, 1, 2, 3].map((v) => <g key={v}><line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke="#E4E1D8" strokeDasharray={v === 0 ? undefined : "3 5"} />
          <text x={L - 8} y={y(v) + 6} fontSize={FS} textAnchor="end" fill="#6C6C6C">{signed(v)}</text></g>)}
        {[0.1, 0.2, 0.3, 0.4, 0.5].map((d) => <text key={d} x={x(d)} y={H - 16} fontSize={FS} textAnchor="middle" fill="#6C6C6C">{pct(d)} off</text>)}
        <rect x={x(Math.max(0.1, depth - 0.025))} y={T} width={Math.max(2, x(depth + 0.025) - x(Math.max(0.1, depth - 0.025)))} height={H - T - B} fill={INK} opacity="0.06" />
        {idx.map((i) => { const d = ev.depth[i], l = ev.lift[i], ok = pays(l, d, margin);
          return <circle key={i} cx={x(d)} cy={y(l)} r={hover === i ? 6 : 2.6} fill={ok ? GREEN : RED} opacity={Math.abs(d - depth) <= 0.025 ? 0.9 : 0.28}
                         onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} onClick={() => onPick(i)} style={{ cursor: "pointer" }} />; })}
        <path d={`M${curve.join("L")}`} fill="none" stroke={FOREST} strokeWidth="3.5" />
        <line x1={x(depth)} x2={x(depth)} y1={T} y2={H - B} stroke={INK} strokeWidth="2" />
      </svg>
      {h !== null ? (
        <div className="pointer-events-none absolute left-2 top-2 rounded-xl border border-rule bg-sheet px-3 py-2 text-sm shadow-sm">
          <div className="font-bold">{ev.item[h]} at {ev.store[h]}</div>
          <div>Week of {ev.week[h]}: {pct(ev.depth[h])} off, sales {signed(ev.lift[h])} vs other stores</div>
          <div className="text-muted">{ev.temporary[h] ? "Price came back after" : "Price stayed down"}</div>
        </div>
      ) : null}
    </div>
  );
}

function ItemSim({ s, depth, margin }: { s: ForecastSeries; depth: number; margin: number }) {
  const [weeks, setWeeks] = useState(new Set([0, 1]));
  const [lift, setLift] = useState(0);
  const price = [...s.price].reverse().find((p) => p !== null) ?? 1;
  const base = s.future.map((u) => u ?? 0);
  const be = breakEven(depth, margin);
  const profit = (units: number, promo: boolean) => units * price * (promo ? margin - depth : margin);
  const rows = base.map((u, i) => { const on = weeks.has(i); const units = on ? u * (1 + lift) : u;
    return { u, units, on, p0: profit(u, false), p1: profit(units, on) }; });
  const d0 = rows.reduce((t, r) => t + r.p0, 0), d1 = rows.reduce((t, r) => t + r.p1, 0);
  const max = Math.max(1, ...rows.map((r) => Math.max(r.u, r.units)));
  const spend = rows.reduce((t, r) => t + (r.on ? r.units * price * depth : 0), 0);
  return (
    <div className="rounded-2xl border border-rule bg-sheet p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="text-xl font-bold">{s.item} at {s.store}</div>
        <div className="text-sm text-muted">next 8 weeks · ${price.toFixed(2)} regular · tap weeks to promote</div>
      </div>
      <div className="mt-4 flex h-44 items-end gap-2" role="group" aria-label="Weeks: tap to put on promotion">
        {rows.map((r, i) => (
          <button key={i} onClick={() => { const n = new Set(weeks); n.has(i) ? n.delete(i) : n.add(i); setWeeks(n); }} aria-pressed={r.on}
                  className="relative flex h-full flex-1 flex-col justify-end rounded-lg hover:bg-paper" title={s.future_weeks[i]}>
            {r.on && r.units > r.u ? <div style={{ height: `${((r.units - r.u) / max) * 100}%`, background: pays(lift, depth, margin) ? GREEN : RED }} className="rounded-t-md opacity-90" /> : null}
            <div style={{ height: `${(Math.min(r.u, r.units) / max) * 100}%` }} className={`${r.on ? "bg-ink" : "bg-rule"} ${r.on && r.units > r.u ? "" : "rounded-t-md"}`} />
            <span className={`mt-1 text-xs ${r.on ? "font-bold" : "text-muted"}`}>{s.future_weeks[i].slice(5)}</span>
          </button>
        ))}
      </div>
      <label className="mt-5 block">
        <div className="flex items-baseline justify-between text-sm"><span className="text-muted">If promoted weeks sell</span>
          <span className="font-bold tabular-nums">{signed(lift)} units</span></div>
        <input type="range" min={-0.5} max={3} step={0.05} value={lift} onChange={(e) => setLift(Number(e.target.value))} className="mt-2 w-full accent-[#28543E]" aria-label="Assumed lift" />
        <div className="relative h-5 text-xs text-muted">
          {isFinite(be) && be <= 3 ? <button onClick={() => setLift(Math.round(be * 20) / 20)} className="absolute -translate-x-1/2 rounded bg-paper px-1 font-bold text-forest"
                 style={{ left: `${((be + 0.5) / 3.5) * 100}%` }}>break-even</button> : null}
        </div>
      </label>
      <div className="mt-3 grid grid-cols-3 gap-3 text-center">
        <div className="rounded-xl bg-paper p-3"><div className="text-xs text-muted">Profit, no promo</div><div className="text-2xl font-light">{money(d0)}</div></div>
        <div className="rounded-xl bg-paper p-3"><div className="text-xs text-muted">Discount paid</div><div className="text-2xl font-light">{money(spend)}</div></div>
        <div className="rounded-xl p-3 text-paper" style={{ background: d1 >= d0 ? FOREST : RED }}><div className="text-xs opacity-80">Profit change</div><div className="text-2xl font-light">{money(d1 - d0)}</div></div>
      </div>
    </div>
  );
}

export function PromoPlanner({ initial, items, series }: { initial: PromoEvidence; items: ForecastBrief[]; series: ForecastSeries | null }) {
  const [ev, setEv] = useState(initial);
  const [dept, setDept] = useState(initial.dept);
  const [depth, setDepth] = useState(0.2);
  const [margin, setMargin] = useState(0.3);
  const [list, setList] = useState(items);
  const [s, setS] = useState(series);
  const [pending, start] = useTransition();
  const be = breakEven(depth, margin);
  const near = useMemo(() => ev.depth.map((d, i) => [d, ev.lift[i]] as const).filter(([d]) => Math.abs(d - depth) <= 0.025), [ev, depth]);
  const paid = near.filter(([d, l]) => pays(l, d, margin)).length;
  const chooseDept = (d: string) => { setDept(d); start(async () => { const e = await loadEvidence(d); if (e) setEv(e); setList(await findItems("", d)); }); };
  return (
    <div className="space-y-6">
      <div className="grid gap-3 md:grid-cols-[1fr_1fr_1.3fr]">
        <Slider label="Price cut" value={depth} set={setDepth} min={0.1} max={0.5} step={0.01} show={pct(depth)} />
        <Slider label="Your margin" value={margin} set={setMargin} min={0.1} max={0.7} step={0.01} show={pct(margin)} />
        <div className="rounded-2xl p-5 text-paper" style={{ background: isFinite(be) ? FOREST : RED }}>
          <div className="text-sm opacity-80">To pay back, promoted weeks must sell</div>
          <div className="font-serif text-5xl font-light">{isFinite(be) ? `+${Math.round(be * 100)}%` : "never"}</div>
          <div className="mt-1 text-sm opacity-80">{isFinite(be) ? "more units than usual" : "the cut is deeper than the margin"}</div>
        </div>
      </div>

      <div className="flex flex-wrap gap-2" role="group" aria-label="Department">
        <button onClick={() => chooseDept("")} aria-pressed={dept === ""} className={`rounded-full px-3 py-1.5 text-sm ${dept === "" ? "bg-ink text-paper" : "bg-sheet hover:bg-rule"}`}>All {Object.values(ev.depts).reduce((a, b) => a + b, 0).toLocaleString()}</button>
        {Object.entries(ev.depts).map(([d, n]) => (
          <button key={d} onClick={() => chooseDept(d)} aria-pressed={dept === d} className={`rounded-full px-3 py-1.5 text-sm ${dept === d ? "bg-ink text-paper" : "bg-sheet hover:bg-rule"}`}>{d.replace("_", " ")} {n.toLocaleString()}</button>
        ))}
      </div>

      <div className={`grid gap-4 lg:grid-cols-[minmax(0,1fr)_16rem] ${pending ? "opacity-60" : ""}`}>
        <div className="rounded-2xl border border-rule bg-sheet p-4">
          <Scatter ev={ev} depth={depth} margin={margin} onPick={(i) => start(async () => { const x = await loadSeries(ev.item[i], ev.store[i]); if (x) setS(x); })} />
          <div className="mt-1 flex flex-wrap gap-4 text-sm text-muted">
            <span><span className="mr-1 inline-block h-2.5 w-2.5 rounded-full" style={{ background: GREEN }} />paid back</span>
            <span><span className="mr-1 inline-block h-2.5 w-2.5 rounded-full" style={{ background: RED }} />did not</span>
            <span><span className="mr-1 inline-block h-0.5 w-5 align-middle" style={{ background: FOREST }} />break-even</span>
            <span>tap a dot to simulate that item</span>
          </div>
        </div>
        <div className="space-y-3">
          <div className="rounded-2xl border border-rule bg-sheet p-5 text-center">
            <div className="text-sm text-muted">Real cuts near {pct(depth)} off</div>
            <div className="font-serif text-6xl font-light" style={{ color: near.length && paid / near.length >= 0.5 ? FOREST : RED }}>
              {near.length ? pct(paid / near.length) : "–"}</div>
            <div className="text-sm text-muted">paid back at {pct(margin)} margin</div>
            <div className="mt-3 h-2.5 overflow-hidden rounded-full bg-[#B3402F]/25"><div className="h-full rounded-full" style={{ width: near.length ? `${(paid / near.length) * 100}%` : 0, background: GREEN, transition: "width 300ms" }} /></div>
            <div className="mt-2 text-xs text-muted">{paid.toLocaleString()} of {near.length.toLocaleString()}</div>
          </div>
          {ev.test ? (
            <div className="rounded-2xl border border-rule bg-sheet p-5">
              <div className="text-sm font-bold">Can a model predict the lift?</div>
              <div className="mt-3 space-y-2 text-sm">
                {(["zero", "history", "model"] as const).map((k) => { const v = ev.test!.mae[k], w = Math.max(...Object.values(ev.test!.mae));
                  return <div key={k} className="grid grid-cols-[4.5rem_1fr_2.5rem] items-center gap-2">
                    <span>{{ zero: "Guess 0", history: "History", model: "ML model" }[k]}</span>
                    <span className="h-2.5 rounded-full bg-paper"><span className="block h-full rounded-full" style={{ width: `${(v / w) * 100}%`, background: k === "model" ? RED : k === "zero" ? INK : "#8C9A2E" }} /></span>
                    <span className="text-right tabular-nums">{v.toFixed(2)}</span></div>; })}
              </div>
              <div className="mt-3 text-xs text-muted">Error on {ev.test.test_cuts} unseen cuts. The model did not beat guessing zero, so this page shows evidence, not a prediction.</div>
            </div>
          ) : null}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[14rem_minmax(0,1fr)]">
        <ul className="space-y-1" aria-label="Items">
          {list.map((b) => (
            <li key={b.item + b.store}><button onClick={() => start(async () => { const x = await loadSeries(b.item, b.store); if (x) setS(x); })}
              className={`w-full rounded-xl px-3 py-2 text-left text-sm ${s && s.item === b.item && s.store === b.store ? "bg-ink text-paper" : "bg-sheet hover:bg-rule"}`}>
              <div className="font-bold">{b.item}</div><div className="opacity-70">{b.store} · ${Math.round(b.dollars).toLocaleString()}/yr</div></button></li>
          ))}
        </ul>
        {s ? <ItemSim key={s.item + s.store} s={s} depth={depth} margin={margin} /> : <p className="text-muted">Pick an item.</p>}
      </div>
    </div>
  );
}
