"use client";

import Link from "next/link";
import { useState } from "react";
import type { ProofCard } from "@/lib/types";
import { DEDUCTION_RATE, HIGHRADIUS, INMAR, INVALID_SHARE, RESOLVED_PER_ANALYST_DAY, analystDays, band, impact,
  posFromSales, salesFromPos } from "@/lib/impact";

const RED = "#B3402F", FOREST = "#28543E", INK = "#1F2A24";
const money = (x: number) => x >= 1e9 ? `$${+(x / 1e9).toFixed(1)}B` : x >= 1e6 ? `$${+(x / 1e6).toFixed(x >= 1e8 ? 0 : 1)}M`
  : x >= 1e3 ? `$${Math.round(x / 1e3).toLocaleString()}K` : `$${Math.round(x).toLocaleString()}`;
const pct = (x: number) => `${+(x * 100).toFixed(1)}%`;

// A slider whose track shows where each source's range sits, so a person sees when they leave it.
function RateSlider({ label, value, max, onChange, bands }: { label: string; value: number; max: number; onChange: (v: number) => void;
  bands: { lo: number; hi: number; name: string }[] }) {
  return (
    <div className="rounded-2xl border border-rule bg-sheet p-5">
      <div className="flex items-baseline justify-between"><span className="text-sm text-muted">{label}</span>
        <span className="font-serif text-4xl font-light">{pct(value)}</span></div>
      <div className="relative mt-4 h-10">
        {bands.map((b, i) => (
          <div key={b.name} className="absolute h-3 rounded-full" title={`${b.name}: ${pct(b.lo)} to ${pct(b.hi)}`}
               style={{ left: `${(b.lo / max) * 100}%`, width: `${((b.hi - b.lo) / max) * 100}%`, top: i * 16 + 22,
                        background: i ? "#9DB8A8" : "#E7B9AE" }} />
        ))}
        <input type="range" min={0} max={max * 1000} step={5} value={Math.round(value * 1000)} aria-label={label}
               onChange={(e) => onChange(+e.target.value / 1000)} className="absolute inset-x-0 top-0 w-full accent-forest" />
      </div>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        {bands.map((b, i) => <span key={b.name}><span className="mr-1 inline-block h-2 w-4 rounded-full align-middle"
          style={{ background: i ? "#9DB8A8" : "#E7B9AE" }} />{b.name} {pct(b.lo)} to {pct(b.hi)}</span>)}
      </div>
    </div>
  );
}

// Three bars, each a magnified slice of the one above: sales, then deductions, then the invalid part.
function Funnel({ sales, d, v }: { sales: number; d: number; v: number }) {
  const r = impact(sales, d, v);
  const W = 1000, bar = 40, gap = 84;
  const rows = [
    { y: 0, label: "Gross sales", value: sales, slice: d, color: "#D8D3C6", sliceColor: RED },
    { y: bar + gap, label: "Deductions retailers take", value: r.deductions, slice: v, color: RED, sliceColor: INK },
    { y: 2 * (bar + gap), label: "Invalid, so disputable", value: r.invalid, slice: 0, color: INK, sliceColor: INK },
  ];
  return (
    <svg viewBox={`0 -40 ${W} ${3 * bar + 2 * gap + 44}`} className="w-full" role="img"
         aria-label={`${money(sales)} sales, ${money(r.deductions)} deducted, ${money(r.invalid)} of it invalid`}>
      {rows.map((row, i) => (
        <g key={row.label}>
          <text x={0} y={row.y - 10} fontSize="30" fill="#6C6C6C">{row.label}</text>
          <text x={W} y={row.y - 10} fontSize="32" fontWeight="700" textAnchor="end" fill={INK}>{money(row.value)}</text>
          <rect x={0} y={row.y} width={W} height={bar} rx="6" fill={row.color} />
          {row.slice > 0 ? <rect x={0} y={row.y} width={Math.max(3, W * row.slice)} height={bar} rx="6" fill={row.sliceColor}
                                 style={{ transition: "width .25s" }} /> : null}
          {i < 2 ? <path d={`M0 ${row.y + bar} L${Math.max(3, W * row.slice)} ${row.y + bar} L${W} ${row.y + bar + gap - 40} L0 ${row.y + bar + gap - 40} Z`}
                         fill={row.sliceColor} opacity="0.08" style={{ transition: "d .25s" }} /> : null}
        </g>
      ))}
    </svg>
  );
}

// Where the person's number sits against what each source's ranges imply.
function Bands({ sales, mine }: { sales: number; mine: number }) {
  const bs = DEDUCTION_RATE.map((d) => ({ name: d.source === INMAR ? "Inmar rates" : "HighRadius rates", b: band(sales, d) }));
  const top = Math.max(mine, ...bs.map((x) => x.b[1])) * 1.08;
  return (
    <div className="space-y-3">
      {bs.map((x, i) => (
        <div key={x.name} className="grid grid-cols-[8.5rem_1fr] items-center gap-3 text-sm">
          <span className="text-muted">{x.name}</span>
          <span className="relative h-6 rounded-full bg-paper">
            <span className="absolute top-0 h-6 rounded-full" style={{ left: `${(x.b[0] / top) * 100}%`, width: `${((x.b[1] - x.b[0]) / top) * 100}%`,
              background: i ? "#9DB8A8" : "#E7B9AE" }} />
            <span className="absolute -top-0.5 text-[11px] text-muted" style={{ left: `calc(${(x.b[0] / top) * 100}% + 6px)`, top: 4 }}>
              {money(x.b[0])} to {money(x.b[1])}</span>
          </span>
        </div>
      ))}
      <div className="grid grid-cols-[8.5rem_1fr] items-center gap-3 text-sm">
        <span className="font-bold">Your settings</span>
        <span className="relative h-6"><span className="absolute top-0 h-6 w-1 rounded bg-ink" style={{ left: `${(mine / top) * 100}%` }} />
          <span className="absolute top-0.5 text-xs font-bold" style={{ left: `calc(${(mine / top) * 100}% + 8px)` }}>{money(mine)}</span></span>
      </div>
    </div>
  );
}

export function Impact({ measured }: { measured: { cash: ProofCard | null; agent: ProofCard | null; reasons: ProofCard | null } }) {
  const [pos, setPos] = useState(posFromSales(50e6));
  const [d, setD] = useState(0.10);
  const [v, setV] = useState(0.075);
  const [perMonth, setPerMonth] = useState("400");
  const sales = salesFromPos(pos);
  const r = impact(sales, d, v);
  const n = Math.max(0, Number(perMonth) || 0);
  const cash = measured.cash?.visual, agent = measured.agent?.visual, reasons = measured.reasons?.visual;
  return (
    <div className="space-y-8">
      <section className="rounded-3xl bg-forest p-6 text-paper md:p-8">
        <label htmlFor="sales" className="text-sm opacity-80">Your annual gross sales</label>
        <div className="font-serif text-6xl font-light md:text-7xl">{money(sales)}</div>
        <input id="sales" type="range" min={0} max={1000} value={pos} onChange={(e) => setPos(+e.target.value)}
               className="mt-4 w-full accent-[#E2F58B]" aria-valuetext={money(sales)} />
        <div className="mt-1 flex justify-between text-xs opacity-70"><span>$1M</span><span>$2B</span></div>
      </section>

      <div className="grid gap-4 md:grid-cols-2">
        <RateSlider label="Share of sales taken as deductions" value={d} max={0.25} onChange={setD}
                    bands={DEDUCTION_RATE.map((x) => ({ lo: x.lo, hi: x.hi, name: x.source === INMAR ? "Inmar" : "HighRadius" }))} />
        <RateSlider label="Share of deduction dollars that are invalid" value={v} max={0.25} onChange={setV}
                    bands={[{ lo: INVALID_SHARE.lo, hi: INVALID_SHARE.hi, name: "HighRadius" }]} />
      </div>

      <section className="grid gap-6 rounded-3xl border border-rule bg-sheet p-6 md:grid-cols-[1.3fr_1fr] md:p-8">
        <Funnel sales={sales} d={d} v={v} />
        <div className="flex flex-col justify-between gap-6">
          <div>
            <div className="text-sm text-muted">Invalid deductions at stake each year</div>
            <div className="font-serif text-6xl font-light" style={{ color: RED }}>{money(r.invalid)}</div>
            <p className="mt-2 text-sm text-muted">Money retailers took that the brand did not owe. It comes back only if someone
              disputes it, with proof, before the retailer&apos;s window closes.</p>
          </div>
          <Bands sales={sales} mine={r.invalid} />
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-[1fr_1.4fr]">
        <div className="rounded-2xl border border-rule bg-sheet p-5">
          <label htmlFor="permonth" className="text-sm text-muted">Deductions you receive a month (type yours)</label>
          <input id="permonth" inputMode="numeric" value={perMonth} onChange={(e) => setPerMonth(e.target.value.replace(/[^0-9]/g, "").slice(0, 7))}
                 className="mt-2 w-full rounded-xl border border-rule bg-paper px-3 py-2 font-serif text-3xl" />
          <div className="mt-4 font-serif text-5xl font-light">{analystDays(n).toFixed(analystDays(n) < 10 ? 1 : 0)}</div>
          <div className="text-sm text-muted">analyst days a month to work them, at about {RESOLVED_PER_ANALYST_DAY} a day (HighRadius)</div>
        </div>
        <div className="rounded-2xl border border-rule bg-sheet p-5">
          <div className="text-sm text-muted">Measured in this system (simulated ledgers built on Walmart M5 sales, not a brand&apos;s books)</div>
          <ul className="mt-3 space-y-3 text-sm">
            {cash && cash.type === "ring" ? <li><Link href="/finance" className="hover:underline"><b className="font-serif text-2xl font-light">{pct(cash.k / cash.n)}</b> of payment
              lines posted with no person, {cash.wrong ?? 0} wrong ({cash.k} of {cash.n}).</Link></li> : null}
            {agent && agent.type === "dots" ? <li><Link href="/investigation" className="hover:underline"><b className="font-serif text-2xl font-light">{agent.right} vs {agent.left}</b> valid
              claims wrongly disputed: the investigation agent against the rules engine on 8 held-out simulated ledgers. Fewer wrongful disputes means less retailer friction.</Link></li> : null}
            {reasons && reasons.type === "ring" ? <li><Link href="/evaluation" className="hover:underline"><b className="font-serif text-2xl font-light">{pct(reasons.k / reasons.n)}</b> of
              deduction reasons read right in wordings no reader trained on.</Link></li> : null}
            {!cash && !agent && !reasons ? <li className="text-muted">Start the API to show the measured rates.</li> : null}
          </ul>
        </div>
      </section>

      <section className="rounded-2xl border border-rule bg-sheet p-5 text-sm">
        <div className="font-bold">What this page will not claim</div>
        <p className="mt-1 text-muted">No company&apos;s revenue, margins or customer results, and no promise of dollars recovered: how much
          of the invalid money comes back depends on a team&apos;s process and deadlines. The industry figures are published by
          vendors, disagree with each other, and do not name all their studies, which is why they are shown as ranges side by side.</p>
        <ul className="mt-3 space-y-1">
          {[INMAR, HIGHRADIUS].map((s) => <li key={s.url}><a href={s.url} target="_blank" rel="noreferrer" className="underline underline-offset-2">{s.name}</a>
            <span className="text-muted">: {s.quote}.</span></li>)}
        </ul>
      </section>
    </div>
  );
}
