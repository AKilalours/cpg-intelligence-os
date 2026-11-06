"use client";

import { useState } from "react";
import type { StpLane, StpPoint } from "@/lib/types";

const NAME: Record<string, string> = {
  exact_reference: "Exact reference",
  explained_short_pay: "Explained short-pay",
  near_reference: "One-keystroke typo",
};

const pct = (x: number, d = 1) => `${(x * 100).toFixed(d)}%`;

function state(l: StpLane): { word: string; tone: string } {
  if (l.mode === "off") return { word: "Gate off", tone: "bg-rule text-ink" };
  if (l.reversals > 0) return { word: "Stopped", tone: "bg-[#B3402F] text-paper" };
  if (l.is_open) return { word: "Posts on its own", tone: "bg-forest text-paper" };
  return { word: "Earning it", tone: "bg-[#B8D63A] text-ink" };
}

function Lane({ l }: { l: StpLane }) {
  const st = state(l);
  const earning = l.mode === "enforce" && !l.is_open && l.reversals === 0 && l.errors === 0;
  const fill = earning ? Math.min(1, l.reviews / l.reviews_needed) : Math.min(1, l.upper_bound / l.max_error_rate);
  return (
    <div className="rounded-2xl border border-rule bg-sheet p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="text-lg font-bold">{NAME[l.lane] ?? l.lane}</div>
        <span className={`rounded-full px-2.5 py-0.5 text-xs font-bold ${st.tone}`}>{st.word}</span>
      </div>
      <div className="mt-4 h-2.5 overflow-hidden rounded-full bg-paper" role="img"
           aria-label={earning ? `${l.reviews} of ${l.reviews_needed} clean reviews` : `error bound ${pct(l.upper_bound)} of a ${pct(l.max_error_rate)} budget`}>
        <div className={`h-full rounded-full ${earning ? "bg-[#B8D63A]" : fill >= 1 ? "bg-[#B3402F]" : "bg-forest"}`}
             style={{ width: `${Math.max(2, fill * 100)}%`, transition: "width 600ms ease" }} />
      </div>
      <div className="mt-2 text-sm text-muted">
        {l.mode === "off" ? "Posts at 90%+ confidence, unchecked (the rule before the gate)"
          : l.reversals > 0 ? `${l.reversals} automatic posting reversed; a person must reopen it`
          : earning ? `${l.reviews} of ${l.reviews_needed} clean reviews to earn it`
          : l.is_open ? `Worst likely error ${pct(l.upper_bound, 2)} within ${pct(l.max_error_rate)}; 1 in ${Math.round(1 / l.audit_rate)} still checked`
          : `${l.errors} wrong in ${l.reviews} reviews: worst likely ${pct(l.upper_bound)}, over the ${pct(l.max_error_rate)} budget`}
      </div>
    </div>
  );
}

type Fault = { lane: string; from: string; gate: { wrong_posted: number; wrong_caught_in_review: number; sent_to_a_person: number };
  no_gate: { wrong_posted: number }; timeline_gate: StpPoint[]; timeline_no_gate: StpPoint[] };

function FaultChart({ faults }: { faults: Record<string, Fault> }) {
  const keys = Object.keys(faults).sort((a, b) => Number(a) - Number(b));
  const [k, setK] = useState(keys[keys.length - 1]);
  const f = faults[k];
  if (!f) return null;
  const W = 640, H = 200, P = 28;
  const n = f.timeline_no_gate.length;
  const max = Math.max(1, ...f.timeline_no_gate.map((p) => p.wrong_posted));
  const x = (i: number) => P + (i / Math.max(1, n - 1)) * (W - 2 * P);
  const y = (v: number) => H - P - (v / max) * (H - 2 * P);
  const path = (pts: StpPoint[]) => pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.wrong_posted).toFixed(1)}`).join(" ");
  const start = Math.max(0, f.timeline_no_gate.findIndex((p) => p.day >= f.from));
  return (
    <div className="rounded-2xl border border-rule bg-sheet p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="text-lg font-bold">A silent fault, replayed on this world</div>
        <div className="flex gap-1" role="group" aria-label="Share of suggestions that turn wrong">
          {keys.map((key) => (
            <button key={key} onClick={() => setK(key)} aria-pressed={k === key}
                    className={`rounded-full px-3 py-1 text-sm ${k === key ? "bg-ink text-paper" : "bg-paper hover:bg-rule"}`}>
              {pct(Number(key), 0)} wrong
            </button>
          ))}
        </div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="mt-3 w-full" role="img"
           aria-label={`Wrong postings: ${f.no_gate.wrong_posted} without the gate, ${f.gate.wrong_posted} with it`}>
        <line x1={x(start)} x2={x(start)} y1={P - 8} y2={H - P} stroke="#9A9A9A" strokeDasharray="3 4" />
        <text x={x(start) + 6} y={P} fontSize="12" fill="#6C6C6C">fault starts</text>
        <line x1={P} x2={W - P} y1={H - P} y2={H - P} stroke="#E4E1D8" />
        <path d={path(f.timeline_no_gate)} fill="none" stroke="#B3402F" strokeWidth="2.5" />
        <path d={path(f.timeline_gate)} fill="none" stroke="#28543E" strokeWidth="2.5" />
        <text x={W - P} y={y(f.no_gate.wrong_posted) - 6} textAnchor="end" fontSize="13" fill="#B3402F" fontWeight="700">
          {f.no_gate.wrong_posted} wrong postings, no gate
        </text>
        <text x={W - P} y={Math.min(H - P - 6, y(f.gate.wrong_posted) - 6)} textAnchor="end" fontSize="13" fill="#28543E" fontWeight="700">
          {f.gate.wrong_posted} with the gate
        </text>
      </svg>
      <div className="text-sm text-muted">
        {f.gate.wrong_caught_in_review} caught by the people the gate sent them to. Reviewer modelled as perfect.
      </div>
    </div>
  );
}

export function Autonomy({ lanes, faults }: { lanes: StpLane[]; faults: Record<string, Fault> | null }) {
  return (
    <>
      <div className="grid gap-3 md:grid-cols-3">
        {lanes.map((l) => <Lane key={l.lane} l={l} />)}
      </div>
      {faults && Object.keys(faults).length ? <div className="mt-3"><FaultChart faults={faults} /></div> : null}
    </>
  );
}
