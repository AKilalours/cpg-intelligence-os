"use client";

import { useEffect, useRef, useState } from "react";

export type RulePoint = {
  report_id: number;
  policy: string;
  invoices: number;
  wrong: number;
  accepted_correct: { k: number; n: number; rate: number | null };
  straight_through: { k: number; n: number; rate: number | null };
  straight_through_complete: { k: number; n: number; rate: number | null } | null;
  review_fields_per_invoice: number | null;
};

// What changed in each version, in one line. The numbers come from the stored reports.
const LESSON: Record<string, string> = {
  "two-readers-1.1.0": "Absence must be checked against the printed labels",
  "two-readers-1.2.0": "Two witnesses for values the scan missed",
  "two-readers-1.3.0": "Every invoice has a seller: never accept it as missing",
  "two-readers-1.4.0": "OCR can no longer veto two models reading the same number",
};
const short = (p: string) => p.replace("two-readers-", "Rule ").replace(/\.0$/, "");

export function RuleStory({ points }: { points: RulePoint[] }) {
  const [i, setI] = useState(points.length - 1);
  const [playing, setPlaying] = useState(false);
  const t = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!playing) return;
    t.current = setInterval(() => setI((x) => {
      if (x >= points.length - 1) { setPlaying(false); return x; }
      return x + 1;
    }), 2200);
    return () => { if (t.current) clearInterval(t.current); };
  }, [playing, points.length]);

  if (points.length === 0) return null;
  const p = points[i];
  const acc = (p.accepted_correct.rate ?? 0) * 100;
  const stp = ((p.straight_through_complete ?? p.straight_through).rate ?? 0) * 100;
  const play = () => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { setI(points.length - 1); return; }
    setI(0); setPlaying(true);
  };

  return (
    <section aria-label="How the invoice rule improved" className="mb-14 rounded-3xl bg-forest p-6 text-paper md:p-10">
      <div className="mb-8 flex flex-wrap items-center justify-between gap-4">
        <h2 className="text-3xl md:text-4xl">Every rule scored once, on invoices it never saw</h2>
        <button onClick={play} className="rounded-lg bg-tag px-4 py-2 font-bold text-ink">{playing ? "Playing…" : "Play the story"}</button>
      </div>

      <ol className="mb-8 flex flex-wrap gap-2" aria-label="Rule versions">
        {points.map((x, j) => (
          <li key={x.report_id}>
            <button onClick={() => { setPlaying(false); setI(j); }} aria-current={j === i ? "step" : undefined}
                    className={"rounded-full border px-4 py-1.5 text-sm transition " +
                      (j === i ? "border-tag bg-tag text-ink" : j < i ? "border-paper/50 text-paper" : "border-paper/25 text-paper/60 hover:border-paper/60")}>
              {short(x.policy)}
            </button>
          </li>
        ))}
      </ol>

      <p key={`l${i}`} className="mb-8 max-w-2xl animate-land font-serif text-2xl font-light text-paper md:text-3xl">{LESSON[p.policy] ?? short(p.policy)}</p>

      <div className="grid gap-8 md:grid-cols-3">
        <Meter label="accepted values right" value={acc} text={`${acc.toFixed(2)}%`} min={97} sub={`${p.accepted_correct.k.toLocaleString()} of ${p.accepted_correct.n.toLocaleString()} · scale 97 to 100%`} />
        <Meter label={p.straight_through_complete ? "complete invoices, no person needed" : "invoices, no person needed"} value={stp} text={`${stp.toFixed(1)}%`} min={0}
               sub={`${(p.straight_through_complete ?? p.straight_through).k} of ${(p.straight_through_complete ?? p.straight_through).n}`} />
        <div>
          <div className="font-serif text-5xl font-light">{p.wrong}</div>
          <div className="mt-1 text-sm text-paper/80">values accepted wrong</div>
          <div className="mt-3 flex max-w-[16rem] flex-wrap gap-1" aria-hidden>
            {Array.from({ length: Math.min(p.wrong, 60) }).map((_, j) => (
              <span key={`${i}-${j}`} className="h-2.5 w-2.5 animate-land rounded-full bg-tag" style={{ animationDelay: `${j * 25}ms` }} />
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

function Meter({ label, value, text, min, sub }: { label: string; value: number; text: string; min: number; sub: string }) {
  const w = Math.max(0, Math.min(100, ((value - min) / (100 - min)) * 100));
  return (
    <div>
      <div className="font-serif text-5xl font-light tabular-nums">{text}</div>
      <div className="mt-1 text-sm text-paper/80">{label}</div>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-paper/15">
        <div className="h-full rounded-full bg-tag transition-[width] duration-700 ease-out" style={{ width: `${w}%` }} />
      </div>
      <div className="mt-1 text-xs text-paper/75">{sub}</div>
    </div>
  );
}
