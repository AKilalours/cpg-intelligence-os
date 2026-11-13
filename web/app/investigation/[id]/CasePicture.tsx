"use client";

import { useState } from "react";
import type { CasePicture as Pic } from "@/lib/types";

const GREEN = "#609D7B", RED = "#B3402F", LIME = "#B8D63A", INK = "#1F2A24", FOREST = "#28543E", RULE = "#E4E1D8";
const DAY = 86400000;
const t = (s: string) => new Date(s + "T00:00:00Z").getTime();
const usd = (s: string | null) => (s === null ? "–" : `$${Number(s).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);

const STAMP: Record<string, { word: string; color: string }> = {
  dispute: { word: "DISPUTE", color: RED }, request: { word: "ASK FOR PROOF", color: "#B08A3E" }, accept: { word: "ACCEPT", color: FOREST },
};

function Stamp({ action, reason }: { action: string; reason: string }) {
  const s = STAMP[action] ?? { word: action.toUpperCase(), color: INK };
  return (
    <div className="flex flex-wrap items-center gap-4">
      <div className="-rotate-3 rounded-xl border-4 px-4 py-1 font-black tracking-widest" style={{ borderColor: s.color, color: s.color }}>{s.word}</div>
      <div className="min-w-0 flex-1 text-sm text-muted">{reason}</div>
    </div>
  );
}

function Money({ m }: { m: Pic["money"] }) {
  const claimed = Number(m.claimed), sup = m.supported === null ? null : Number(m.supported), rec = m.recoverable === null ? 0 : Number(m.recoverable);
  const w = (v: number) => `${claimed ? Math.max(0, Math.min(100, (v / claimed) * 100)) : 0}%`;
  return (
    <div>
      <div className="flex items-baseline justify-between"><span className="text-sm text-muted">Claimed {usd(m.claimed)}</span>
        <span className="font-serif text-4xl font-light" style={{ color: rec > 0 ? RED : FOREST }}>{rec > 0 ? `${usd(m.recoverable)} back` : "nothing to recover"}</span></div>
      <div className="mt-2 flex h-6 overflow-hidden rounded-full bg-paper" role="img" aria-label={`Claimed ${usd(m.claimed)}, supported ${usd(m.supported)}, recoverable ${usd(m.recoverable)}`}>
        {sup !== null ? <div style={{ width: w(sup), background: GREEN }} title={`supported ${usd(m.supported)}`} /> : null}
        <div style={{ width: w(rec), background: `repeating-linear-gradient(135deg, ${RED}, ${RED} 6px, #c95c4c 6px, #c95c4c 12px)` }} title={`recoverable ${usd(m.recoverable)}`} />
      </div>
      <div className="mt-1 flex gap-4 text-xs text-muted">
        <span><span className="mr-1 inline-block h-2 w-2 rounded-full" style={{ background: GREEN }} />backed by evidence</span>
        <span><span className="mr-1 inline-block h-2 w-2 rounded-full" style={{ background: RED }} />not backed</span>
      </div>
    </div>
  );
}

function Countdown({ deadline, asOf }: { deadline: string; asOf: string }) {
  const days = Math.round((t(deadline) - t(asOf)) / DAY), total = 180;
  const open = days > 0;
  return (
    <div className="flex items-center gap-3">
      <svg viewBox="0 0 44 44" className="h-11 w-11" aria-hidden><circle cx="22" cy="22" r="18" fill="none" stroke={RULE} strokeWidth="6" />
        <circle cx="22" cy="22" r="18" fill="none" stroke={open ? (days < 14 ? RED : FOREST) : "#9A9A9A"} strokeWidth="6"
                strokeDasharray={113} strokeDashoffset={113 * (1 - Math.max(0, Math.min(1, days / total)))} transform="rotate(-90 22 22)" /></svg>
      <div><div className="text-2xl font-light tabular-nums">{open ? `${days} days` : "closed"}</div><div className="text-xs text-muted">to dispute</div></div>
    </div>
  );
}

function Timeline({ p }: { p: Pic }) {
  const [hover, setHover] = useState<number | null>(null);
  if (!p.window) return null;
  const [lo, hi] = p.window.map(t);
  // Text sized for the viewBox so it stays legible when the strip shrinks to a phone's width.
  const W = 760, L = 16, R = 16, rowH = 36, FS = 22;
  const x = (s: string | number) => L + ((typeof s === "string" ? t(s) : s) - lo) / (hi - lo) * (W - L - R);
  const weeks: number[] = [];
  for (let d = lo; d <= hi; d += 7 * DAY) weeks.push(d);
  const pMax = Math.max(1, ...p.prices.map((r) => r.price)), pMin = Math.min(...p.prices.map((r) => r.price), pMax) * 0.9;
  const uMax = Math.max(1, ...p.pos.map((r) => r.units ?? 0));
  const yP = (v: number) => 150 + (1 - (v - pMin) / (pMax - pMin || 1)) * 80;
  const promos = [...p.promotions].sort((a, b) => Number(b.this_store) - Number(a.this_store)).slice(0, 4);
  const top = 40 + Math.max(1, promos.length) * rowH;
  const priceAt = (d: number) => p.prices.find((r) => Math.abs(t(r.week) - d) < 4 * DAY);
  const posAt = (d: number) => p.pos.find((r) => Math.abs(t(r.week) - d) < 4 * DAY);
  const H = top + 330;
  const shift = top;
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="The claim period, promotions on file, shelf prices and the retailer's sales, week by week"
           onMouseLeave={() => setHover(null)}
           onMouseMove={(e) => { const r = e.currentTarget.getBoundingClientRect(); const d = lo + ((e.clientX - r.left) / r.width * W - L) / (W - L - R) * (hi - lo);
             setHover(weeks.reduce((b, w) => (Math.abs(w - d) < Math.abs(b - d) ? w : b), weeks[0])); }}>
        {p.claim.period_start && p.claim.period_end ? (<>
          <rect x={x(p.claim.period_start)} y={0} width={x(p.claim.period_end) - x(p.claim.period_start)} height={H} fill={INK} opacity="0.05" />
          <rect x={x(p.claim.period_start)} y={2} width={Math.max(8, x(p.claim.period_end) - x(p.claim.period_start))} height={26} rx="13" fill={INK} />
          {(() => { const a = x(p.claim.period_start!), b = x(p.claim.period_end!), label = `claimed · ${Number(p.claim.claimed_units ?? 0)} units`;
            // A short period cannot hold its label: put it beside the band, on whichever side has room.
            const inside = b - a > 230, right = W - R - b > 240;
            return <text x={inside ? a + 12 : right ? b + 10 : a - 10} y={22} fontSize={FS} textAnchor={inside || right ? "start" : "end"}
                         fill={inside ? "white" : INK} fontWeight="700">{label}</text>; })()}
        </>) : null}
        {promos.map((pr, i) => (
          <g key={pr.promotion_id}>
            <rect x={x(pr.start)} y={40 + i * rowH} width={Math.max(8, x(pr.end) - x(pr.start))} height={26} rx="13" fill={pr.this_store ? GREEN : RULE} />
            <text x={Math.min(x(pr.start) + 10, W - R - 300)} y={60 + i * rowH} fontSize={FS} fill={pr.this_store && x(pr.end) - x(pr.start) > 300 ? "white" : "#3F4A44"}>
              promo{pr.this_store ? " · this store" : ` · store ${pr.store}`} · ${Number(pr.allowance).toFixed(2)}/unit</text>
          </g>
        ))}
        {promos.length === 0 ? <text x={L} y={60} fontSize={FS} fill={RED} fontWeight="700">no promotion on file for this item</text> : null}
        <g transform={`translate(0 ${shift - 120})`}>
          {p.prices.length > 1 ? <path d={p.prices.map((r, i) => `${i ? "L" : "M"}${x(r.week).toFixed(1)},${yP(r.price).toFixed(1)}`).join("")} fill="none" stroke={FOREST} strokeWidth="3" /> : null}
          {p.prices.map((r) => p.drops.includes(r.week) ? <circle key={r.week} cx={x(r.week)} cy={yP(r.price)} r="7" fill={LIME} stroke={INK} strokeWidth="1.5" /> : null)}
          <text x={L} y={140} fontSize={FS} fill="#6C6C6C">shelf price{p.drops.length ? ` · ${p.drops.length} promo-priced week${p.drops.length > 1 ? "s" : ""}` : " · no price drop"}</text>
        </g>
        <g transform={`translate(0 ${shift + 150})`}>
          <text x={L} y={0} fontSize={FS} fill="#6C6C6C">retailer&apos;s sales (as provided)</text>
          {weeks.map((d) => { const r = posAt(d); const inPeriod = p.claim.period_start && p.claim.period_end && d >= t(p.claim.period_start) - 3 * DAY && d <= t(p.claim.period_end);
            return r && r.units !== null ? <rect key={d} x={x(d) - 8} y={150 - (r.units / uMax) * 120} width={16} height={(r.units / uMax) * 120} fill={inPeriod ? INK : "#9A9A9A"} rx="3" />
              : inPeriod ? <text key={d} x={x(d)} y={146} fontSize={FS + 8} textAnchor="middle" fill={RED} fontWeight="700">?</text> : null; })}
        </g>
        {hover !== null ? <line x1={x(hover)} x2={x(hover)} y1={0} y2={H} stroke={INK} strokeOpacity="0.35" /> : null}
      </svg>
      {hover !== null ? (() => { const pr = priceAt(hover), ps = posAt(hover), wk = new Date(hover).toISOString().slice(0, 10);
        return (
          <div className="pointer-events-none absolute right-2 top-2 rounded-xl border border-rule bg-sheet px-3 py-2 text-sm shadow-sm">
            <div className="font-bold">Week of {wk}</div>
            <div>{pr ? `Shelf $${pr.price.toFixed(2)}${p.drops.includes(pr.week) ? " (promo price)" : ""}` : "No shelf price"}</div>
            <div>{ps ? (ps.units === null ? "Sales missing" : `Sold ${ps.units}`) : "No sales row"}</div>
          </div>); })() : null}
      <div className="mt-1 flex flex-wrap gap-4 text-xs text-muted">
        <span><span className="mr-1 inline-block h-2 w-4 rounded-full" style={{ background: GREEN }} />promotion at this store</span>
        <span><span className="mr-1 inline-block h-2.5 w-2.5 rounded-full" style={{ background: LIME }} />promo-priced week</span>
        <span><span className="mr-1 font-bold" style={{ color: RED }}>?</span>sales week missing</span>
      </div>
    </div>
  );
}

export function CasePicture({ p, action, reason, asOf }: { p: Pic; action: string; reason: string; asOf: string }) {
  return (
    <section className="mb-8 space-y-5 rounded-2xl border border-rule bg-sheet p-5" aria-label="The case at a glance">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <Stamp action={action} reason={reason} />
        <Countdown deadline={p.claim.dispute_deadline} asOf={asOf} />
      </div>
      <Money m={p.money} />
      <Timeline p={p} />
    </section>
  );
}
