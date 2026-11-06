"use client";

import { useState } from "react";
import type { StpLane } from "@/lib/types";

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

type Mc = { wrong_posted_with_gate: { median: number; p90: number }; wrong_posted_no_gate: { median: number };
  sent_to_a_person: { median: number } };
export type Fresh = { lines: number; runs: number; audit_rate: number; seeds: number[]; results: Record<string, Mc> };

const SCEN: [string, string][] = [
  ["healthy", "Healthy"], ["fault_5pct", "5% quietly wrong"], ["fault_20pct", "20% quietly wrong"],
  ["bad_release_10pct", "Bad release"],
];

function FreshProof({ fresh }: { fresh: Fresh }) {
  const [k, setK] = useState("fault_5pct");
  const r = fresh.results[k];
  if (!r) return null;
  const max = Math.max(1, r.wrong_posted_no_gate.median);
  const bar = (v: number) => `${Math.max(v > 0 ? 1.5 : 0, (v / max) * 100)}%`;
  return (
    <div className="rounded-2xl border border-rule bg-sheet p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="text-lg font-bold">{fresh.seeds.length} unseen worlds, {fresh.runs} rolls each</div>
        <div className="flex flex-wrap gap-1" role="group" aria-label="Scenario">
          {SCEN.map(([key, label]) => (
            <button key={key} onClick={() => setK(key)} aria-pressed={k === key}
                    className={`rounded-full px-3 py-1 text-sm ${k === key ? "bg-ink text-paper" : "bg-paper hover:bg-rule"}`}>
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="mt-5 space-y-3" aria-live="polite">
        <Row label="Wrong postings, no gate" v={r.wrong_posted_no_gate.median} w={bar(r.wrong_posted_no_gate.median)} color="bg-[#B3402F]" />
        <Row label="With the gate (typical)" v={r.wrong_posted_with_gate.median} w={bar(r.wrong_posted_with_gate.median)} color="bg-forest" />
        <Row label="With the gate (bad roll)" v={r.wrong_posted_with_gate.p90} w={bar(r.wrong_posted_with_gate.p90)} color="bg-[#609D7B]" />
      </div>
      <div className="mt-4 text-sm text-muted">
        {Math.round(r.sent_to_a_person.median).toLocaleString()} of {fresh.lines.toLocaleString()} lines went to a person.
        1 in {Math.round(1 / fresh.audit_rate)} spot-checked once a lane opens. Reviewer modelled as perfect.
      </div>
    </div>
  );
}

function Row({ label, v, w, color }: { label: string; v: number; w: string; color: string }) {
  return (
    <div className="grid grid-cols-[11rem_1fr_3rem] items-center gap-3 text-sm">
      <span>{label}</span>
      <div className="h-3 rounded-full bg-paper"><div className={`h-full rounded-full ${color}`} style={{ width: w, transition: "width 500ms ease" }} /></div>
      <span className="text-right font-bold tabular-nums">{Math.round(v)}</span>
    </div>
  );
}

export function Autonomy({ lanes, fresh }: { lanes: StpLane[]; fresh: Fresh | null }) {
  return (
    <>
      <div className="grid gap-3 md:grid-cols-3">
        {lanes.map((l) => <Lane key={l.lane} l={l} />)}
      </div>
      {fresh?.results ? <div className="mt-3"><FreshProof fresh={fresh} /></div> : null}
    </>
  );
}
