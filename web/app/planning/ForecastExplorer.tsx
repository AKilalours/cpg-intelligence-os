"use client";

import { useEffect, useMemo, useState, useTransition } from "react";
import type { ForecastBrief, ForecastSeries, ForecastSummary } from "@/lib/types";
import { findSeries, getSeries } from "./forecastActions";

const METHOD: Record<string, { label: string; color: string; dash?: string }> = {
  lgbm: { label: "LightGBM (shipped)", color: "#28543E" },
  ses: { label: "Exponential smoothing", color: "#8C9A2E" },
  tsb: { label: "TSB (intermittent)", color: "#6F8FAF" },
  seasonal_naive: { label: "Same week last year", color: "#B3402F", dash: "5 4" },
  naive: { label: "Last week", color: "#9A9A9A", dash: "2 4" },
  ma13: { label: "13-week average", color: "#B08A3E" }, croston: { label: "Croston", color: "#7B6FAF" },
  sba: { label: "SBA", color: "#AF6F9B" }, ma4: { label: "4-week average", color: "#C9A66B" },
};
const CLASS: Record<string, string> = { smooth: "Smooth", erratic: "Erratic", intermittent: "Intermittent", lumpy: "Lumpy" };
const SORT: [string, string][] = [["dollars", "Top sellers"], ["win", "LightGBM won most"], ["loss", "LightGBM lost most"]];
const pct = (x: number, d = 1) => `${(x * 100).toFixed(d)}%`;
const money = (x: number) => `$${Math.round(x).toLocaleString()}`;

function Chart({ s, show }: { s: ForecastSeries; show: Set<string> }) {
  const [hover, setHover] = useState<number | null>(null);
  const H = s.history.length, T = s.actual.length, F = s.future.length, N = H + T + F;
  const vals = [...s.history, ...s.actual, ...s.test_hi, ...s.future_hi, ...Object.entries(s.test).filter(([k]) => show.has(k)).flatMap(([, v]) => v)]
    .filter((v): v is number => v !== null);
  const max = Math.max(1, ...vals) * 1.08;
  // Text sized for the viewBox so it stays readable when the chart shrinks to a phone's width.
  const W = 760, Ht = 330, L = 54, R = 14, Tp = 30, B = 40, FS = 20;
  const x = (i: number) => L + (i / (N - 1)) * (W - L - R);
  const y = (v: number) => Tp + (1 - v / max) * (Ht - Tp - B);
  // A path that breaks at missing weeks (an item not on the shelf) instead of drawing through them.
  const line = (arr: (number | null)[], off: number): string => {
    let d = "", pen = false;
    arr.forEach((v, i) => {
      if (v === null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${x(off + i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  };
  const band = (lo: (number | null)[], hi: (number | null)[], off: number) => {
    const top = hi.map((v, i) => `${x(off + i)},${y(v ?? 0)}`), bot = lo.map((v, i) => `${x(off + i)},${y(v ?? 0)}`).reverse();
    return `M${top.join("L")}L${bot.join("L")}Z`;
  };
  const weekAt = (i: number) => (i < H + T ? s.weeks[i] : s.future_weeks[i - H - T]);
  const ticks = [0, Math.round(max / 2), Math.round(max)];
  const hv = hover === null ? null : {
    week: weekAt(hover),
    sold: hover < H ? s.history[hover] : hover < H + T ? s.actual[hover - H] : null,
    fc: hover >= H && hover < H + T ? s.test.lgbm[hover - H] : hover >= H + T ? s.future[hover - H - T] : null,
    lo: hover >= H && hover < H + T ? s.test_lo[hover - H] : hover >= H + T ? s.future_lo[hover - H - T] : null,
    hi: hover >= H && hover < H + T ? s.test_hi[hover - H] : hover >= H + T ? s.future_hi[hover - H - T] : null,
  };
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${Ht}`} className="w-full touch-none" role="img"
           aria-label={`Weekly units for ${s.item} at ${s.store}: a year of history, 8 test weeks and 8 future weeks`}
           onMouseLeave={() => setHover(null)}
           onMouseMove={(e) => { const r = e.currentTarget.getBoundingClientRect();
             const i = Math.round(((e.clientX - r.left) / r.width * W - L) / (W - L - R) * (N - 1)); setHover(Math.max(0, Math.min(N - 1, i))); }}>
        {ticks.map((t) => <g key={t}><line x1={L} x2={W - R} y1={y(t)} y2={y(t)} stroke="#E4E1D8" /><text x={L - 8} y={y(t) + 6} fontSize={FS} textAnchor="end" fill="#6C6C6C">{t}</text></g>)}
        <rect x={x(H) - 2} y={Tp} width={x(H + T - 1) - x(H) + 4} height={Ht - Tp - B} fill="#F4F2EC" />
        <text x={x(H) + (x(H + T) - x(H)) / 2} y={Tp - 8} fontSize={FS} textAnchor="middle" fill="#6C6C6C">test</text>
        <text x={x(H + T) + (x(N - 1) - x(H + T)) / 2} y={Tp - 8} fontSize={FS} textAnchor="middle" fill="#6C6C6C">next</text>
        <path d={band(s.test_lo, s.test_hi, H)} fill="#28543E" opacity="0.13" />
        <path d={band(s.future_lo, s.future_hi, H + T)} fill="#28543E" opacity="0.13" />
        <path d={line(s.history, 0)} fill="none" stroke="#1F2A24" strokeOpacity="0.45" strokeWidth="1.5" />
        <path d={line(s.actual, H)} fill="none" stroke="#1F2A24" strokeWidth="2.2" />
        {s.actual.map((v, i) => v === null ? null : <circle key={i} cx={x(H + i)} cy={y(v)} r="2.6" fill="#1F2A24" />)}
        {Object.entries(s.test).filter(([k]) => show.has(k)).map(([k, v]) => (
          <path key={k} d={line(v, H)} fill="none" stroke={METHOD[k]?.color ?? "#999"} strokeWidth={k === "lgbm" ? 2.6 : 1.8} strokeDasharray={METHOD[k]?.dash} />
        ))}
        <path d={line([s.actual.at(-1) ?? s.history.at(-1) ?? 0, ...s.future], H + T - 1)} fill="none" stroke="#28543E" strokeWidth="2.6" strokeDasharray="6 4" />
        {[0, 26, 52].map((i) => i < N ? <text key={i} x={Math.max(x(i), L + 30)} y={Ht - 10} fontSize={FS} textAnchor="middle" fill="#6C6C6C">{weekAt(i)?.slice(0, 7)}</text> : null)}
        {hover !== null ? <line x1={x(hover)} x2={x(hover)} y1={Tp} y2={Ht - B} stroke="#1F2A24" strokeOpacity="0.3" /> : null}
      </svg>
      {hv ? (
        <div className="pointer-events-none absolute right-2 top-2 rounded-xl border border-rule bg-sheet px-3 py-2 text-sm shadow-sm">
          <div className="font-bold">Week of {hv.week}</div>
          {hv.sold !== null ? <div>Sold {hv.sold}</div> : null}
          {hv.fc !== null && hv.fc !== undefined ? <div>Forecast {hv.fc} <span className="text-muted">({hv.lo} to {hv.hi})</span></div> : null}
        </div>
      ) : null}
    </div>
  );
}

export function ForecastExplorer({ summary, initialList, initial }: { summary: ForecastSummary; initialList: ForecastBrief[]; initial: ForecastSeries | null }) {
  const [q, setQ] = useState(""), [store, setStore] = useState(""), [cls, setCls] = useState(""), [sort, setSort] = useState("dollars");
  const [list, setList] = useState(initialList);
  const [s, setS] = useState(initial);
  const [show, setShow] = useState(new Set(["lgbm", "ses", "seasonal_naive"]));
  const [pending, start] = useTransition();
  useEffect(() => {
    const t = setTimeout(() => start(async () => setList(await findSeries(q, store, cls, sort))), 250);
    return () => clearTimeout(t);
  }, [q, store, cls, sort]);
  const open = (b: ForecastBrief) => start(async () => setS(await getSeries(b.item, b.store)));
  const board = useMemo(() => s ? Object.entries(s.test_rmsse).filter(([, v]) => v !== null).sort((a, b) => (a[1] as number) - (b[1] as number)) : [], [s]);
  const p = summary.test?.paired?.lgbm_vs_ses;
  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-2xl bg-forest p-5 text-paper"><div className="font-serif text-4xl font-light">{summary.series.toLocaleString()}</div>
          <div className="mt-2 text-sm text-paper/80">real item-store series, forecast 8 weeks ahead</div></div>
        <div className="rounded-2xl bg-forest p-5 text-paper"><div className="font-serif text-4xl font-light">{p ? pct(-p.relative) : "–"}</div>
          <div className="mt-2 text-sm text-paper/80">more accurate than exponential smoothing{p ? ` (${pct(-p.ci95[1] / (summary.test!.scores.ses.wrmsse))} to ${pct(-p.ci95[0] / (summary.test!.scores.ses.wrmsse))})` : ""}, on weeks scored once</div></div>
        <div className="rounded-2xl bg-forest p-5 text-paper"><div className="font-serif text-4xl font-light">{summary.intervals ? pct(summary.intervals.test_coverage) : "–"}</div>
          <div className="mt-2 text-sm text-paper/80">of real weeks fell inside the 80% range</div></div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search item, e.g. FOODS_3_090" aria-label="Search item"
               className="w-60 rounded-full border border-rule bg-sheet px-4 py-2 text-sm" />
        <select value={store} onChange={(e) => setStore(e.target.value)} aria-label="Store" className="rounded-full border border-rule bg-sheet px-3 py-2 text-sm">
          <option value="">All stores</option>{summary.stores.map((x) => <option key={x}>{x}</option>)}
        </select>
        {Object.entries(CLASS).map(([k, label]) => (
          <button key={k} onClick={() => setCls(cls === k ? "" : k)} aria-pressed={cls === k}
                  className={`rounded-full px-3 py-1.5 text-sm ${cls === k ? "bg-ink text-paper" : "bg-sheet hover:bg-rule"}`}>{label} {summary.classes[k]?.toLocaleString()}</button>
        ))}
      </div>
      <div className="flex flex-wrap gap-1" role="group" aria-label="Order">
        {SORT.map(([k, label]) => (
          <button key={k} onClick={() => setSort(k)} aria-pressed={sort === k}
                  className={`rounded-full px-3 py-1 text-sm ${sort === k ? "bg-forest text-paper" : "bg-paper hover:bg-rule"}`}>{label}</button>
        ))}
      </div>

      <div className="grid gap-5 lg:grid-cols-[17rem_minmax(0,1fr)]">
        <ul className={`max-h-[34rem] space-y-1 overflow-auto ${pending ? "opacity-60" : ""}`} aria-label="Series">
          {list.length === 0 ? <li className="text-sm text-muted">No series match.</li> : list.map((b) => {
            const on = s && s.item === b.item && s.store === b.store;
            const gain = b.ses_rmsse && b.lgbm_rmsse !== null ? 1 - b.lgbm_rmsse / b.ses_rmsse : null;
            return (
              <li key={b.item + b.store}>
                <button onClick={() => open(b)} className={`w-full rounded-xl px-3 py-2 text-left text-sm ${on ? "bg-ink text-paper" : "bg-sheet hover:bg-rule"}`}>
                  <div className="font-bold">{b.item}</div>
                  <div className={on ? "text-paper/75" : "text-muted"}>{b.store} · {CLASS[b.class] ?? b.class} · {money(b.dollars)}/yr
                    {gain !== null ? ` · ${gain >= 0 ? "+" : ""}${pct(gain, 0)}` : ""}</div>
                </button>
              </li>
            );
          })}
        </ul>

        <div className="min-w-0 rounded-2xl border border-rule bg-sheet p-5">
          {!s ? <p className="text-muted">Pick a series.</p> : (
            <>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <div className="text-xl font-bold">{s.item} at {s.store}</div>
                <div className="text-sm text-muted">{CLASS[s.class] ?? s.class} demand over the last year · {money(s.dollars)} sold</div>
              </div>
              <Chart s={s} show={show} />
              <div className="mt-4 grid gap-2">
                <div className="text-sm font-bold">Error on this series&apos;s test weeks (lower is better). Tap to draw.</div>
                {board.map(([k, v]) => {
                  const worst = board.at(-1)![1] as number;
                  return (
                    <button key={k} onClick={() => { const n = new Set(show); n.has(k) ? n.delete(k) : n.add(k); setShow(n); }}
                            aria-pressed={show.has(k)} className="grid grid-cols-[minmax(0,1fr)_5.5rem_3rem] items-center gap-3 text-left text-sm sm:grid-cols-[13rem_minmax(0,1fr)_3.5rem]">
                      <span className={`truncate ${show.has(k) ? "font-bold" : "text-muted"}`}>
                        <span className="mr-2 inline-block h-2.5 w-2.5 rounded-full" style={{ background: METHOD[k]?.color }} />{METHOD[k]?.label ?? k}</span>
                      <span className="h-2.5 rounded-full bg-paper"><span className="block h-full rounded-full" style={{ width: `${((v as number) / worst) * 100}%`, background: METHOD[k]?.color, opacity: show.has(k) ? 1 : 0.35 }} /></span>
                      <span className="text-right tabular-nums">{(v as number).toFixed(2)}</span>
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </div>
      <p className="text-sm text-muted">Real Walmart M5 sales. Test weeks {summary.test_weeks[0]} to {summary.test_weeks[1]}, scored once after the model was frozen.
        The shaded band is an 80% range learned from earlier weeks only. Future weeks use the known calendar; the price plan there is unknown.</p>
    </div>
  );
}
