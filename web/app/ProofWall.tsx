"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { ProofCard } from "@/lib/types";

const FOREST = "#28543E", GREEN = "#609D7B", RED = "#B3402F", RULE = "#E4E1D8";

function useGrow(): number {
  // 0 then 1 after mount, so rings and bars sweep in once; reduced motion skips the transition (globals.css).
  const [g, setG] = useState(0);
  useEffect(() => { const t = setTimeout(() => setG(1), 60); return () => clearTimeout(t); }, []);
  return g;
}

function Ring({ k, n, wrong, note, target }: { k: number; n: number; wrong?: number; note?: string; target?: number }) {
  const g = useGrow(), r = 52, c = 2 * Math.PI * r, share = n ? k / n : 0;
  const pct = share >= 0.995 ? (share * 100).toFixed(2) : (share * 100).toFixed(1);
  return (
    <div className="flex items-center gap-4">
      <svg viewBox="0 0 128 128" className="h-28 w-28 shrink-0" aria-hidden>
        <circle cx="64" cy="64" r={r} fill="none" stroke={RULE} strokeWidth="12" />
        <circle cx="64" cy="64" r={r} fill="none" stroke={FOREST} strokeWidth="12" strokeLinecap="round"
                strokeDasharray={c} strokeDashoffset={c * (1 - share * g)} transform="rotate(-90 64 64)"
                style={{ transition: "stroke-dashoffset 1100ms cubic-bezier(.2,.7,.2,1)" }} />
        {target ? <line x1="64" y1="6" x2="64" y2="22" stroke={RED} strokeWidth="3" transform={`rotate(${target * 360} 64 64)`} /> : null}
      </svg>
      <div>
        <div className="font-serif text-4xl font-light tabular-nums">{pct}%</div>
        <div className="text-sm text-muted tabular-nums">{k.toLocaleString()} of {n.toLocaleString()}</div>
        {wrong !== undefined ? <div className="mt-1 inline-block rounded-full bg-forest px-2 py-0.5 text-xs font-bold text-paper">{wrong} wrong</div> : null}
        {note ? <div className="mt-1 inline-block rounded-full bg-forest px-2 py-0.5 text-xs font-bold text-paper">{note}</div> : null}
      </div>
    </div>
  );
}

function Dots({ left, left_label, right, right_label, p }: { left: number; left_label: string; right: number; right_label: string; p: number }) {
  const g = useGrow();
  const field = (n: number, color: string) => (
    <div className="flex max-w-[9rem] flex-wrap gap-1">
      {Array.from({ length: n }, (_, i) => (
        <span key={i} className="h-3 w-3 rounded-full" style={{ background: color, opacity: g, transform: `scale(${g})`,
              transition: `all 300ms ease ${i * 35}ms` }} />))}
    </div>
  );
  return (
    <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3">
      <div>{field(left, RED)}<div className="mt-2 text-sm text-muted">{left_label} <b className="text-ink">{left}</b></div></div>
      <div className="font-serif text-3xl font-light text-muted">vs</div>
      <div>{field(right, FOREST)}<div className="mt-2 text-sm text-muted">{right_label} <b className="text-ink">{right}</b></div></div>
      <div className="col-span-3 text-xs text-muted">chance this is luck: {p < 0.001 ? "under 1 in 1,000" : p.toFixed(3)}</div>
    </div>
  );
}

function Versus({ left, left_label, right, right_label, lower_better, honest_no, unit = "" }: { left: number; left_label: string; right: number; right_label: string; lower_better?: boolean; honest_no?: boolean; unit?: string }) {
  const g = useGrow(), max = Math.max(left, right) || 1;
  const rightWins = lower_better ? right < left : right < left;
  const rightColor = honest_no ? RED : rightWins ? FOREST : RED;
  const bar = (v: number, label: string, color: string, bold: boolean) => (
    <div>
      <div className="flex justify-between text-sm"><span className={bold ? "font-bold" : "text-muted"}>{label}</span><span className="tabular-nums">{v.toLocaleString()}</span></div>
      <div className="mt-1 h-4 rounded-full bg-paper"><div className="h-full rounded-full" style={{ width: `${(v / max) * 100 * g}%`, background: color, transition: "width 900ms cubic-bezier(.2,.7,.2,1)" }} /></div>
    </div>
  );
  const change = left ? (right - left) / left : 0;
  return (
    <div className="space-y-3">
      <div className="font-serif text-3xl font-light" style={{ color: rightColor }}>
        {Math.abs(Math.round(change * 100))}% {change < 0 ? (unit === "error" ? "less" : "fewer") : "more"} {unit}</div>
      {bar(left, left_label, "#9A9A9A", false)}
      {bar(right, right_label, rightColor, true)}
    </div>
  );
}

function Card({ c }: { c: ProofCard }) {
  const [flip, setFlip] = useState(false);
  const v = c.visual;
  return (
    <div className="h-64 [perspective:1200px]">
      <button onClick={() => setFlip(!flip)} aria-pressed={flip} aria-label={`${c.title}: tap for how it was measured`}
              className="relative h-full w-full text-left transition-transform duration-500 [transform-style:preserve-3d]"
              style={{ transform: flip ? "rotateY(180deg)" : "none" }}>
        <div className="absolute inset-0 flex flex-col justify-between rounded-2xl border border-rule bg-sheet p-5 [backface-visibility:hidden]">
          <div className="text-lg font-bold">{c.title}</div>
          {v.type === "ring" ? <Ring {...v} /> : v.type === "dots" ? <Dots {...v} /> : <Versus {...v} />}
          <div className="text-xs text-muted">tap to see how</div>
        </div>
        <div className="absolute inset-0 flex flex-col justify-between rounded-2xl bg-forest p-5 text-paper [backface-visibility:hidden] [transform:rotateY(180deg)]">
          <div className="text-lg font-bold">{c.title}</div>
          <p className="text-sm text-paper/85">{c.method}</p>
          <Link href={c.href} onClick={(e) => e.stopPropagation()} className="self-start rounded-full bg-paper px-4 py-1.5 text-sm font-bold text-ink">Open</Link>
        </div>
      </button>
    </div>
  );
}

export function ProofWall({ cards }: { cards: ProofCard[] }) {
  return <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{cards.map((c) => <Card key={c.key} c={c} />)}</div>;
}
