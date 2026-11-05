"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { count, fromCents, money, toCents } from "@/lib/format";
import type { FlowStream, MoneyFlow as Flow } from "@/lib/types";

type Meta = { label: string; color: string; href: string; unit: string; live?: boolean };
const META: Record<string, Meta> = {
  engine: { label: "Posted by the engine", color: "#609D7B", href: "/finance", unit: "payments" },
  person: { label: "Approved by a person", color: "#28543E", href: "/finance", unit: "payments" },
  waiting: { label: "Waiting for you", color: "#B8D63A", href: "/finance", unit: "payments", live: true },
  unmatched: { label: "No safe match", color: "#C9776A", href: "/finance", unit: "payments" },
  dispute: { label: "Dispute it", color: "#B3402F", href: "/finance", unit: "claims", live: true },
  evidence: { label: "Needs evidence", color: "#9A9A9A", href: "/finance", unit: "claims" },
  valid: { label: "Valid, accept", color: "#609D7B", href: "/finance", unit: "claims" },
  closed: { label: "Window closed", color: "#5B5B5B", href: "/finance", unit: "claims" },
  decided: { label: "Decided by a person", color: "#28543E", href: "/finance", unit: "claims" },
};

const X0 = 240, X1 = 640, GAP = 14, LABEL = 34;

function layout(streams: FlowStream[], top: number, band: number) {
  const total = streams.reduce((t, s) => t + Number(s.amount), 0) || 1;
  let src = top, dst = top;
  return streams.map((s) => {
    const w = Math.max(4, (band * Number(s.amount)) / total);
    const y0 = src + w / 2, y1 = dst + w / 2;
    src += w; dst += Math.max(w, LABEL) + GAP;
    return { s, w, y0, y1, d: `M${X0},${y0} C${(X0 + X1) / 2},${y0} ${(X0 + X1) / 2},${y1} ${X1},${y1}` };
  });
}

export function MoneyFlow({ flow }: { flow: Flow }) {
  const router = useRouter();
  const [hover, setHover] = useState<string | null>(null);
  const cash = layout(flow.cash, 20, 150);
  const cashBottom = Math.max(...cash.map((c) => c.y1 + c.w / 2), 170);
  const ded = layout(flow.deductions, cashBottom + 60, 110);
  const height = Math.max(...ded.map((c) => c.y1 + c.w / 2), cashBottom + 170) + 20;
  const sum = (xs: FlowStream[]) => fromCents(xs.reduce((t, s) => t + toCents(s.amount), 0n));
  const src = (rows: typeof cash, title: string, total: string, n: number) => {
    const y = rows[0]?.y0 - rows[0]?.w / 2, h = rows.reduce((t, r) => t + r.w, 0);
    return (
      <g>
        <rect x={X0 - 8} y={y} width="8" height={h} rx="3" fill="#1F2A24" />
        <text x={X0 - 18} y={y + h / 2 - 8} textAnchor="end" className="fill-ink font-serif text-[26px]">
          {money(total).replace(/\.\d\d$/, "")}
        </text>
        <text x={X0 - 18} y={y + h / 2 + 14} textAnchor="end" className="fill-muted text-[13px]">{title} · {count(n)}</text>
      </g>
    );
  };

  return (
    <div className="rounded-3xl border border-rule bg-sheet p-4 md:p-6">
      <svg viewBox={`0 0 1000 ${height}`} className="w-full" role="img" aria-label="Where the retailers' money went">
        {src(cash, "cash paid", sum(flow.cash), flow.cash.reduce((t, s) => t + s.count, 0))}
        {src(ded, "deductions taken", sum(flow.deductions), flow.deductions.reduce((t, s) => t + s.count, 0))}
        {[...cash, ...ded].map(({ s, w, y1, d }, i) => {
          const m = META[s.key], dim = hover !== null && hover !== s.key;
          return (
            <g key={s.key} className="cursor-pointer" opacity={dim ? 0.25 : 1} style={{ transition: "opacity 200ms" }}
               onMouseEnter={() => setHover(s.key)} onMouseLeave={() => setHover(null)} onClick={() => router.push(m.href)}>
              <path d={d} fill="none" stroke={m.color} strokeOpacity="0.5" strokeWidth={w} pathLength={1}
                    strokeDasharray="1" className="animate-draw" style={{ ["--len" as string]: 1, ["--off" as string]: 0, animationDelay: `${i * 90}ms` }} />
              <path d={d} fill="none" stroke={m.color} strokeWidth={Math.min(Math.max(2, w * 0.3), 5)} strokeLinecap="round"
                    strokeDasharray="1 23" className="animate-flow" style={{ animationDuration: m.live ? "700ms" : "1800ms" }} />
              <rect x={X1} y={y1 - w / 2} width="8" height={w} rx="3" fill={m.color} />
              <text x={X1 + 20} y={y1 - 2} dominantBaseline="auto" className="fill-ink text-[15px] font-semibold">
                {money(s.amount).replace(/\.\d\d$/, "")}
                <tspan className="fill-muted text-[13px] font-normal" dx="8">{m.label}</tspan>
              </text>
              <text x={X1 + 20} y={y1 + 15} className="fill-muted text-[12px]">{count(s.count)} {m.unit}{s.recoverable && Number(s.recoverable) > 0 && (s.key === "dispute" || s.key === "closed") ? ` · ${money(s.recoverable).replace(/\.\d\d$/, "")} ${s.key === "closed" ? "was recoverable, now lost" : "recoverable"}` : ""}</text>
            </g>
          );
        })}
      </svg>
      <p className="mt-2 h-5 text-sm text-muted" aria-live="polite">
        {hover ? (() => {
          const s = [...flow.cash, ...flow.deductions].find((x) => x.key === hover)!;
          return `${META[hover].label}: ${money(s.amount)} across ${count(s.count)} ${META[hover].unit}` +
            (s.recoverable && Number(s.recoverable) > 0 && hover === "dispute" ? `, ${money(s.recoverable)} recoverable` : "") + ". Click to act.";
        })() : "Hover a stream for the exact amount. Click to act on it."}
      </p>
    </div>
  );
}
