"use client";

import { useMemo, useRef, useState } from "react";
import type { SalesSeries } from "@/lib/types";

const W = 1000, H = 260, OV = 56, PADL = 44, PADR = 52;
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
type Smooth = 1 | 7 | 28;

// Units and prices become numbers only to draw them; every figure shown is read from the data.
export function SeriesExplorer({ series }: { series: SalesSeries }) {
  const pts = useMemo(() => series.points.map((p) => ({
    date: p.obs_date, units: Number(p.units), price: p.shelf_price === null ? null : Number(p.shelf_price),
    dow: (new Date(p.obs_date + "T00:00:00Z").getUTCDay() + 6) % 7,
  })), [series.points]);
  const n = pts.length;
  const daily = series.granularity === "day";
  const [range, setRange] = useState<[number, number]>([Math.max(0, n - (daily ? 365 : 52)), n - 1]);
  const [smooth, setSmooth] = useState<Smooth>(daily ? 7 : 1);
  const [hover, setHover] = useState<number | null>(null);
  const drag = useRef<number | null>(null);
  const ovRef = useRef<SVGSVGElement | null>(null);

  const avg = useMemo(() => {
    if (smooth === 1) return pts.map((p) => p.units);
    let run = 0;
    return pts.map((p, i) => { run += p.units; if (i >= smooth) run -= pts[i - smooth].units; return run / Math.min(i + 1, smooth); });
  }, [pts, smooth]);

  const [a, b] = range;
  const view = pts.slice(a, b + 1);
  const umax = Math.max(1, ...pts.slice(a, b + 1).map((p) => p.units));
  const prices = view.map((p) => p.price).filter((x): x is number => x !== null);
  const pmin = prices.length ? Math.min(...prices) : 0, pmax = prices.length ? Math.max(...prices) : 1;
  const x = (i: number) => PADL + ((i - a) / Math.max(1, b - a)) * (W - PADL - PADR);
  const yU = (u: number) => H - 22 - (u / umax) * (H - 40);
  const yP = (p: number) => pmax === pmin ? 40 : H - 22 - ((p - pmin) / (pmax - pmin)) * (H - 60) - 10;

  const line = (vals: number[]) => vals.map((v, k) => `${k ? "L" : "M"}${x(a + k).toFixed(1)},${yU(v).toFixed(1)}`).join("");
  const priceSteps = view.map((p, k) => p.price === null ? null : `${x(a + k).toFixed(1)},${yP(p.price).toFixed(1)}`).filter(Boolean).join(" ");
  const changes = view.map((p, k) => k > 0 && p.price !== null && view[k - 1].price !== null && p.price !== view[k - 1].price
    ? { k, down: p.price < (view[k - 1].price as number) } : null).filter((c): c is { k: number; down: boolean } => c !== null);

  // Facts about the selected window, for the planner and for the model choice in Phase 5.
  const zero = view.filter((p) => p.units === 0).length;
  const total = view.reduce((t, p) => t + p.units, 0);
  const byDow = DAYS.map((_, d) => { const xs = view.filter((p) => p.dow === d); return xs.length ? xs.reduce((t, p) => t + p.units, 0) / xs.length : 0; });
  const dmax = Math.max(1, ...byDow);
  const zeroShare = view.length ? zero / view.length : 0;
  const byPrice = useMemo(() => {
    const m = new Map<number, { t: number; c: number }>();
    for (const p of view) if (p.price !== null) { const e = m.get(p.price) ?? { t: 0, c: 0 }; e.t += p.units; e.c += 1; m.set(p.price, e); }
    return [...m.entries()].sort((x, y) => x[0] - y[0]).map(([price, e]) => ({ price, avg: e.t / e.c, n: e.c }));
  }, [view]);
  const bmax = Math.max(1, ...byPrice.map((x) => x.avg));

  const ovX = (i: number) => PADL + (i / Math.max(1, n - 1)) * (W - PADL - PADR);
  const fromOv = (clientX: number) => {
    const r = ovRef.current!.getBoundingClientRect();
    const fx = ((clientX - r.left) / r.width) * W;
    return Math.round(Math.min(n - 1, Math.max(0, ((fx - PADL) / (W - PADL - PADR)) * (n - 1))));
  };
  const ovMax = Math.max(1, ...pts.map((p) => p.units));
  const ovPath = pts.map((p, i) => `${i ? "L" : "M"}${ovX(i).toFixed(1)},${(OV - 4 - (p.units / ovMax) * (OV - 10)).toFixed(1)}`).join("");
  const preset = (len: number | null) => setRange(len === null ? [0, n - 1] : [Math.max(0, n - len), n - 1]);
  const h = hover !== null ? pts[hover] : null;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        {(daily ? [[91, "3M"], [365, "1Y"], [730, "2Y"], [null, "All"]] : [[13, "3M"], [52, "1Y"], [104, "2Y"], [null, "All"]] as const).map(([len, l]) => (
          <button key={l} onClick={() => preset(len as number | null)}
                  className="rounded-full border border-rule bg-sheet px-3 py-1 hover:border-forest">{l}</button>
        ))}
        {daily ? (
          <span className="ml-3 flex overflow-hidden rounded-full border border-rule">
            {([1, 7, 28] as Smooth[]).map((s) => (
              <button key={s} onClick={() => setSmooth(s)} aria-pressed={smooth === s}
                      className={`px-3 py-1 ${smooth === s ? "bg-forest text-white" : "bg-sheet hover:bg-paper"}`}>
                {s === 1 ? "Daily" : `${s}-day avg`}
              </button>
            ))}
          </span>
        ) : null}
        <span className="ml-auto text-muted">{pts[a]?.date} to {pts[b]?.date}</span>
      </div>

      <div className="rounded-2xl border border-rule bg-sheet p-3">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full touch-none" role="img" aria-label="Units sold and shelf price"
             onMouseLeave={() => setHover(null)}
             onMouseMove={(e) => {
               const r = e.currentTarget.getBoundingClientRect();
               const fx = ((e.clientX - r.left) / r.width) * W;
               setHover(Math.min(b, Math.max(a, Math.round(a + ((fx - PADL) / (W - PADL - PADR)) * (b - a)))));
             }}>
          {[0.25, 0.5, 0.75, 1].map((f) => (
            <g key={f}>
              <line x1={PADL} x2={W - PADR} y1={yU(umax * f)} y2={yU(umax * f)} stroke="#E4E1D8" />
              <text x={PADL - 6} y={yU(umax * f)} textAnchor="end" dominantBaseline="central" className="fill-muted text-[11px]">{Math.round(umax * f)}</text>
            </g>
          ))}
          {changes.map((c) => (
            <line key={c.k} x1={x(a + c.k)} x2={x(a + c.k)} y1="14" y2={H - 22} stroke={c.down ? "#B3402F" : "#2881D4"} strokeOpacity="0.35" strokeDasharray="2 3" />
          ))}
          {smooth > 1 ? <path d={line(view.map((p) => p.units))} fill="none" stroke="#609D7B" strokeOpacity="0.25" strokeWidth="1" /> : null}
          <path key={`${a}-${b}-${smooth}`} d={line(avg.slice(a, b + 1))} fill="none" stroke="#28543E" strokeWidth="2" pathLength={1}
                strokeDasharray="1" className="animate-draw" style={{ ["--len" as string]: 1, ["--off" as string]: 0 }} />
          {priceSteps ? <polyline points={priceSteps} fill="none" stroke="#2881D4" strokeWidth="1.5" /> : null}
          {prices.length ? (
            <>
              <text x={W - PADR + 6} y={yP(pmax)} dominantBaseline="central" className="fill-sky text-[11px]">${pmax.toFixed(2)}</text>
              <text x={W - PADR + 6} y={yP(pmin)} dominantBaseline="central" className="fill-sky text-[11px]">${pmin.toFixed(2)}</text>
            </>
          ) : null}
          {h ? (
            <g pointerEvents="none">
              <line x1={x(hover!)} x2={x(hover!)} y1="10" y2={H - 22} stroke="#1F2A24" strokeOpacity="0.4" />
              <circle cx={x(hover!)} cy={yU(h.units)} r="4" fill="#28543E" />
              <rect x={Math.min(x(hover!) + 10, W - PADR - 170)} y="12" width="164" height="54" rx="8" fill="#1F2A24" />
              <text x={Math.min(x(hover!) + 22, W - PADR - 158)} y="32" className="fill-white text-[12px]">{DAYS[h.dow]} {h.date}</text>
              <text x={Math.min(x(hover!) + 22, W - PADR - 158)} y="52" className="fill-tag text-[13px] font-semibold">
                {h.units} units{h.price !== null ? ` · $${h.price.toFixed(2)}` : ""}
              </text>
            </g>
          ) : null}
        </svg>

        <svg ref={ovRef} viewBox={`0 0 ${W} ${OV}`} className="mt-1 w-full cursor-ew-resize touch-none select-none" aria-label="Drag to choose the period"
             onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); drag.current = fromOv(e.clientX); setRange([drag.current, drag.current]); }}
             onPointerMove={(e) => { if (drag.current === null) return; const i = fromOv(e.clientX); setRange([Math.min(drag.current, i), Math.max(drag.current, i)]); }}
             onPointerUp={() => { drag.current = null; setRange(([p, q]) => q - p < 7 ? [Math.max(0, p - 14), Math.min(n - 1, q + 14)] : [p, q]); }}>
          <path d={ovPath} fill="none" stroke="#9A9A9A" strokeWidth="1" />
          <rect x={ovX(a)} y="0" width={Math.max(2, ovX(b) - ovX(a))} height={OV} fill="#E5FF87" fillOpacity="0.45" stroke="#28543E" rx="4" />
        </svg>
        <p className="mt-1 text-xs text-muted">Drag across the strip to zoom. Dashed lines mark price changes: red down, blue up.</p>
      </div>

      <div className="mt-5 grid gap-3 md:grid-cols-3">
        <div className="rounded-2xl border border-rule bg-sheet p-4">
          <p className="font-serif text-4xl tabular-nums">{Math.round(total).toLocaleString("en-US")}</p>
          <p className="text-sm text-muted">units in this window · {(total / Math.max(1, view.length)).toFixed(1)} per {daily ? "day" : "week"}</p>
        </div>
        <div className={`rounded-2xl border p-4 ${zeroShare > 0.3 ? "border-ink bg-tag" : "border-rule bg-sheet"}`}>
          <p className="font-serif text-4xl tabular-nums">{Math.round(zeroShare * 100)}%</p>
          <p className="text-sm">{daily ? "days" : "weeks"} with zero sales{zeroShare > 0.3 ? ": intermittent demand, the case for Croston/TSB" : ""}</p>
        </div>
        {daily ? (
          <div className="rounded-2xl border border-rule bg-sheet p-4">
            <div className="flex h-14 items-end gap-1.5" aria-label="Average units by weekday">
              {byDow.map((v, d) => (
                <div key={d} className="flex flex-1 flex-col items-center gap-1">
                  <div className="w-full rounded-t bg-forest/80 transition-all duration-500" style={{ height: `${Math.max(4, (v / dmax) * 44)}px` }} title={`${DAYS[d]}: ${v.toFixed(1)}`} />
                  <span className="text-[10px] text-muted">{DAYS[d][0]}</span>
                </div>
              ))}
            </div>
            <p className="mt-1 text-sm text-muted">average by weekday · {changes.length} price change{changes.length === 1 ? "" : "s"}</p>
          </div>
        ) : null}
        {byPrice.length > 1 ? (
          <div className={`rounded-2xl border border-rule bg-sheet p-4 ${daily ? "md:col-span-3" : ""}`}>
            <div className="flex h-14 items-end gap-1.5" aria-label="Average units at each shelf price">
              {byPrice.slice(0, 12).map((x) => (
                <div key={x.price} className="flex flex-1 flex-col items-center gap-1">
                  <div className="w-full rounded-t bg-sky/80 transition-all duration-500" style={{ height: `${Math.max(4, (x.avg / bmax) * 44)}px` }}
                       title={`$${x.price.toFixed(2)}: ${x.avg.toFixed(1)} units on average over ${x.n} ${daily ? "days" : "weeks"}`} />
                  <span className="text-[10px] text-muted">${x.price.toFixed(2)}</span>
                </div>
              ))}
            </div>
            <p className="mt-1 text-sm text-muted">average units at each shelf price · {changes.length} price change{changes.length === 1 ? "" : "s"}</p>
            <p className="text-xs text-muted">Raw averages, not a price effect: stockouts and seasons mix in.</p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
