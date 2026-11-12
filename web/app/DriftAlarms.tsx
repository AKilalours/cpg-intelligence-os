"use client";

import { useState, useTransition } from "react";
import { useReviewer } from "@/components/useReviewer";
import type { DriftAlarm, DriftStatus } from "@/lib/types";
import { clearAlarm } from "./driftActions";

const STREAM: Record<string, string> = { cash: "Cash lines", deductions: "Deduction claims", prices: "Shelf prices" };
const FEATURE: Record<string, string> = {
  reference_shape: "reference format", engine_outcome: "how lines match", amount_size: "payment sizes",
  netted_short_pay: "short-pays", reason_reader_sure: "reason wording", reason_category: "reason mix",
  code: "deduction codes", shelf_price_drop: "price drops",
};
const pct = (x: number) => `${Math.round(x * 100)}%`;

function Alarm({ a }: { a: DriftAlarm }) {
  const [name, setName] = useReviewer();
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const top = a.moved[0];
  return (
    <div className="rounded-2xl border-2 border-[#B3402F] bg-sheet p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="text-lg font-bold">{STREAM[a.stream]}: {FEATURE[a.feature] ?? a.feature} changed</div>
        <div className="text-sm text-muted">through {a.checked_through}</div>
      </div>
      {top ? (
        <p className="mt-2">
          <span className="font-bold">{top.value === "other" ? "Something new" : `"${top.value}"`}</span> went from{" "}
          {pct(top.baseline)} to <span className="font-bold">{pct(top.now)}</span> of recent {a.stream === "cash" ? "lines" : "rows"}.
          {a.stream === "cash" ? " Every cash match goes to a person until this is cleared." : ""}
        </p>
      ) : null}
      <form className="mt-4 flex flex-wrap gap-2"
            onSubmit={(e) => { e.preventDefault(); setErr(null);
              start(async () => { const r = await clearAlarm(a.drift_alarm_id, name, note); if (!r.ok) setErr(r.error ?? "failed"); }); }}>
        <input aria-label="Your name" placeholder="Your name" value={name} onChange={(e) => setName(e.target.value)}
               className="w-36 rounded-full border border-rule bg-paper px-3 py-1.5 text-sm" />
        <input aria-label="What you checked" placeholder="What you checked" value={note} onChange={(e) => setNote(e.target.value)}
               className="min-w-0 flex-1 rounded-full border border-rule bg-paper px-3 py-1.5 text-sm" />
        <button disabled={pending} className="rounded-full bg-ink px-4 py-1.5 text-sm font-bold text-paper disabled:opacity-50">
          {pending ? "Clearing" : "Clear alarm"}
        </button>
      </form>
      {err ? <p role="alert" className="mt-2 text-sm text-[#B3402F]">{err}</p> : null}
    </div>
  );
}

export function DriftAlarms({ s }: { s: DriftStatus }) {
  const ev = s.evaluation;
  const det = ev ? Object.values(ev.detection) : [];
  const fa = ev?.false_alarms ? Object.values(ev.false_alarms) : [];
  const weeks = fa.reduce((t, f) => t + f.weeks_checked, 0), falseWeeks = fa.reduce((t, f) => t + f.weeks_with_an_alarm, 0);
  return (
    <div className="space-y-3">
      {s.open.length === 0 ? (
        <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-rule bg-sheet p-5">
          <span className="h-3 w-3 rounded-full bg-forest" aria-hidden />
          <span className="font-bold">No alarms.</span>
          <span className="text-muted">Cash, claims and shelf prices are checked weekly against the data trust was earned on.</span>
        </div>
      ) : s.open.map((a) => <Alarm key={a.drift_alarm_id} a={a} />)}
      {det.length ? (
        <p className="text-sm text-muted">
          In testing ({ev!.kind === "drift_fresh" ? "unseen worlds" : "the main world"}): {det.filter((d) => d.detected === d.rolls).length} of {det.length} kinds
          of change caught every time, {falseWeeks} false alarms in {weeks} healthy weeks.
        </p>
      ) : null}
    </div>
  );
}
