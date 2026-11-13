"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import type { StpReplay } from "@/lib/types";
import { runReplay } from "./replayActions";

const RED = "#B3402F", FOREST = "#28543E", INK = "#1F2A24", LIME = "#B8D63A";

export function GateReplay() {
  const [fault, setFault] = useState(0.2), [audit, setAudit] = useState(0.2), [seed, setSeed] = useState(1);
  const [r, setR] = useState<StpReplay | null>(null);
  const [k, setK] = useState(0);                          // how many days have played
  const [pending, start] = useTransition();
  const raf = useRef<number | null>(null);

  const play = (data: StpReplay) => {
    if (raf.current) cancelAnimationFrame(raf.current);
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) { setK(data.days.length); return; }
    const t0 = performance.now(), dur = 4500;
    const step = (now: number) => { const f = Math.min(1, (now - t0) / dur); setK(Math.round(f * data.days.length));
      if (f < 1) raf.current = requestAnimationFrame(step); };
    setK(0); raf.current = requestAnimationFrame(step);
  };
  const go = (s = seed) => start(async () => { const d = await runReplay(fault, audit, s); if (d) { setR(d); play(d); } });
  useEffect(() => { go(); return () => { if (raf.current) cancelAnimationFrame(raf.current); }; }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  const W = 760, H = 300, L = 50, R = 20, T = 20, B = 40, FS = 20;
  const n = r?.days.length ?? 1, max = Math.max(1, ...(r?.no_gate ?? [1]));
  const x = (i: number) => L + (i / Math.max(1, n - 1)) * (W - L - R);
  const y = (v: number) => T + (1 - v / max) * (H - T - B);
  const path = (arr: number[]) => arr.slice(0, k).map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const at = (d: string | null) => (r && d ? r.days.findIndex((x) => x >= d) : -1);
  const fi = at(r?.fault_from ?? null), oi = at(r?.opened ?? null), ci = at(r?.closed ?? null);
  const shown = (i: number) => i >= 0 && i < k;
  const nowNo = r && k ? r.no_gate[k - 1] : 0, nowGate = r && k ? r.gate[k - 1] : 0;

  return (
    <div className="rounded-2xl border border-rule bg-sheet p-5">
      <div className="grid gap-3 md:grid-cols-[1fr_1fr_auto]">
        <label className="block"><div className="flex justify-between text-sm"><span className="text-muted">Hidden fault</span><b className="tabular-nums">{Math.round(fault * 100)}% wrong</b></div>
          <input type="range" min={0} max={0.3} step={0.01} value={fault} onChange={(e) => setFault(Number(e.target.value))} className="w-full accent-[#B3402F]" aria-label="Hidden fault size" /></label>
        <label className="block"><div className="flex justify-between text-sm"><span className="text-muted">Spot-check</span><b className="tabular-nums">1 in {Math.round(1 / audit)}</b></div>
          <input type="range" min={0.02} max={0.5} step={0.01} value={audit} onChange={(e) => setAudit(Number(e.target.value))} className="w-full accent-[#28543E]" aria-label="Spot-check rate" /></label>
        <div className="flex items-end gap-2">
          <button onClick={() => go()} disabled={pending} className="rounded-full bg-ink px-5 py-2 font-bold text-paper disabled:opacity-50">{pending ? "…" : "Play"}</button>
          <button onClick={() => { const s = seed + 1; setSeed(s); go(s); }} disabled={pending} className="rounded-full border border-rule px-4 py-2 text-sm" title="Another roll of the dice">Roll again</button>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3">
        <div className="rounded-xl p-3 text-paper" style={{ background: RED }}><div className="text-xs opacity-80">Wrong postings, no gate</div>
          <div className="font-serif text-4xl font-light tabular-nums">{nowNo}</div></div>
        <div className="rounded-xl p-3 text-paper" style={{ background: FOREST }}><div className="text-xs opacity-80">With the error budget</div>
          <div className="font-serif text-4xl font-light tabular-nums">{nowGate}</div></div>
      </div>

      {r ? (
        <svg viewBox={`0 0 ${W} ${H}`} className="mt-3 w-full" role="img" aria-label="Wrong postings over time with and without the gate">
          {fi >= 0 ? <rect x={x(fi)} y={T} width={x(n - 1) - x(fi)} height={H - T - B} fill={RED} opacity={shown(fi) ? 0.07 : 0} style={{ transition: "opacity 300ms" }} /> : null}
          {fi >= 0 && shown(fi) ? <text x={x(fi) + 8} y={T + 22} fontSize={FS} fill={RED} fontWeight="700">fault starts</text> : null}
          <line x1={L} x2={W - R} y1={y(0)} y2={y(0)} stroke="#E4E1D8" />
          <text x={L - 8} y={y(max) + 6} fontSize={FS} textAnchor="end" fill="#6C6C6C">{max}</text>
          <text x={L - 8} y={y(0) + 6} fontSize={FS} textAnchor="end" fill="#6C6C6C">0</text>
          <path d={path(r.no_gate)} fill="none" stroke={RED} strokeWidth="4" />
          <path d={path(r.gate)} fill="none" stroke={FOREST} strokeWidth="4" />
          {oi >= 0 && shown(oi) ? <g><circle cx={x(oi)} cy={y(r.gate[oi])} r="10" fill={LIME} stroke={INK} strokeWidth="2" />
            <text x={x(oi)} y={y(r.gate[oi]) - 18} fontSize={FS} textAnchor="middle" fill={INK} fontWeight="700">trusted</text></g> : null}
          {ci >= 0 && shown(ci) ? <g><rect x={x(ci) - 9} y={y(r.gate[ci]) - 9} width="18" height="18" fill={RED} />
            <text x={x(ci)} y={y(r.gate[ci]) - 18} fontSize={FS} textAnchor="middle" fill={RED} fontWeight="700">stopped</text></g> : null}
          <text x={L} y={H - 10} fontSize={FS} fill="#6C6C6C">{r.days[0]?.slice(0, 7)}</text>
          <text x={W - R} y={H - 10} fontSize={FS} textAnchor="end" fill="#6C6C6C">{r.days[n - 1]?.slice(0, 7)}</text>
        </svg>
      ) : <div className="mt-3 h-48 animate-pulse rounded-xl bg-paper" />}
      {r ? <p className="mt-1 text-xs text-muted">Simulation on the main world: {r.lines.toLocaleString()} lines, {r.to_a_person.toLocaleString()} checked by a person, {r.caught} wrong ones caught there. The answer key plays the reviewer.</p> : null}
    </div>
  );
}
