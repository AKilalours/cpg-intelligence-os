"use client";

import { useCallback, useEffect, useRef, useState, useTransition } from "react";
import { align } from "@/lib/align";
import { fromCents, money, toCents } from "@/lib/format";
import type { CashAppItem } from "@/lib/types";
import { useReviewer } from "@/components/useReviewer";
import { decideCash } from "./actions";

type Done = { id: number; status: "approved" | "rejected" };

function Ring({ value }: { value: number }) {
  const r = 26, len = 2 * Math.PI * r, off = len * (1 - value);
  const tone = value >= 0.9 ? "#609D7B" : value >= 0.7 ? "#28543E" : "#B3402F";
  return (
    <svg viewBox="0 0 64 64" className="h-20 w-20 -rotate-90" aria-label={`Engine confidence ${Math.round(value * 100)}%`}>
      <circle cx="32" cy="32" r={r} fill="#fff" stroke="#E4E1D8" strokeWidth="6" />
      <circle key={value} cx="32" cy="32" r={r} fill="none" stroke={tone} strokeWidth="6" strokeLinecap="round"
              strokeDasharray={len} className="animate-draw"
              style={{ strokeDashoffset: off, ["--len" as string]: len, ["--off" as string]: off }} />
      <text x="32" y="32" transform="rotate(90 32 32)" textAnchor="middle" dominantBaseline="central"
            className="fill-ink font-serif text-[15px]">{Math.round(value * 100)}%</text>
    </svg>
  );
}

function chips(e: Record<string, unknown> | null): string[] {
  if (!e) return [];
  const name: Record<string, string> = { reference: "Ref", amount: "Amount", caution: "Careful", short_pay: "Short-pay", risk: "Risk" };
  return Object.entries(e).filter(([k]) => k in name).map(([k, v]) => `${name[k]}: ${String(v)}`);
}

const OP: Record<string, string> = { same: "bg-paper text-ink", change: "bg-[#B3402F] text-white", insert: "bg-[#B8D63A] text-ink",
  delete: "bg-[#B3402F] text-white", swap: "bg-[#B8D63A] text-ink" };

// The two references as letter tiles, aligned: matching characters plain, the mistyped, dropped or swapped
// ones lit. "One keystroke away" becomes something you see, not a sentence.
function RefTiles({ paid, invoice }: { paid: string | null; invoice: string | null }) {
  if (!paid || !invoice) {
    return <p className="text-sm"><span className="rounded bg-[#B3402F] px-2 py-0.5 font-bold text-white">no reference</span> matched on amount alone</p>;
  }
  const cells = align(paid.toUpperCase(), invoice.toUpperCase());
  const diff = cells.filter((c) => c.op !== "same").length;
  const row = (side: "a" | "b") => (
    <div className="flex flex-wrap gap-0.5">
      {cells.map((c, k) => (
        <span key={k} className={`grid h-8 w-6 place-items-center rounded font-mono text-base font-bold ${c[side] === null ? "border border-dashed border-[#B3402F] bg-transparent" : OP[c.op]}`}>
          {c[side] ?? ""}</span>))}
    </div>
  );
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2"><span className="w-20 text-xs text-muted">remittance</span>{row("a")}</div>
      <div className="flex items-center gap-2"><span className="w-20 text-xs text-muted">invoice</span>{row("b")}</div>
      <div className="pl-[5.5rem] text-xs font-bold" style={{ color: diff ? "#B3402F" : "#28543E" }}>
        {diff === 0 ? "identical" : `${diff} character${diff > 1 ? "s" : ""} differ`}</div>
    </div>
  );
}

function AmountBars({ paid, billed }: { paid: number; billed: number | null }) {
  const max = Math.max(paid, billed ?? 0) || 1;
  return (
    <div className="space-y-1.5">
      {[["paid", paid, "#28543E"], ["invoiced", billed, "#9A9A9A"]].map(([label, v, color]) => (
        <div key={label as string} className="grid grid-cols-[4.5rem_1fr] items-center gap-2 text-xs">
          <span className="text-muted">{label}</span>
          <div className="h-4 rounded-full bg-paper">{v === null ? null : <div className="h-full rounded-full" style={{ width: `${((v as number) / max) * 100}%`, background: color as string, transition: "width 500ms" }} />}</div>
        </div>
      ))}
    </div>
  );
}

export function MatchDeck({ items, total }: { items: CashAppItem[]; total: number }) {
  const [reviewer, setReviewer] = useReviewer();
  const [i, setI] = useState(0);
  const [done, setDone] = useState<Done[]>([]);
  const [leaving, setLeaving] = useState<"l" | "r" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const [dx, setDx] = useState(0);
  const drag = useRef<{ x: number; id: number } | null>(null);
  const queue = items.filter((x) => !done.some((d) => d.id === x.remittance_line_id));
  const card = queue[i % Math.max(queue.length, 1)];

  const act = useCallback((action: "approve" | "reject") => {
    if (!card || pending) return;
    setErr(null);
    start(async () => {
      const r = await decideCash(card.remittance_line_id, action, reviewer);
      if (!r.ok) { setErr(r.error); return; }
      setLeaving(action === "approve" ? "r" : "l");
      setTimeout(() => {
        setDone((d) => [...d, { id: card.remittance_line_id, status: r.status as Done["status"] }]);
        setLeaving(null);
      }, 280);
    });
  }, [card, pending, reviewer]);

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, textarea") || document.body.dataset.drawer === "open") return;
      if (e.key === "a") act("approve");
      else if (e.key === "r") act("reject");
      else if (e.key === "ArrowRight") setI((x) => x + 1);
      else if (e.key === "ArrowLeft") setI((x) => Math.max(0, x - 1));
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [act]);

  const approved = done.filter((d) => d.status === "approved").length;
  if (!card) {
    return (
      <div className="rounded-2xl border border-rule bg-sheet p-10 text-center animate-rise">
        <p className="font-serif text-3xl">Queue clear</p>
        <p className="mt-2 text-muted">{approved} approved, {done.length - approved} rejected in this session. Every one is in the audit log.</p>
      </div>
    );
  }
  // Drag the card: right approves, left rejects, past 140 px; anything shorter springs back. The same decision
  // as the buttons, attributed to the reviewer named above.
  const down = (e: React.PointerEvent) => { if ((e.target as HTMLElement).closest("button, input")) return;
    drag.current = { x: e.clientX, id: e.pointerId }; (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); };
  const move = (e: React.PointerEvent) => { if (drag.current) setDx(e.clientX - drag.current.x); };
  const up = () => { if (!drag.current) return; const d = dx; drag.current = null; setDx(0);
    if (d > 140) act("approve"); else if (d < -140) act("reject"); };
  const paid = toCents(card.amount), billed = toCents(card.invoice_amount ?? null);
  const gap = card.invoice_amount ? paid - billed : 0n;
  const conf = card.confidence === null ? 0 : Number(card.confidence);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
        <span className="font-semibold">{total - done.length} to decide{total > items.length ? `, largest ${items.length} here` : ""}</span>
        <span className="flex gap-1" aria-hidden>
          {queue.slice(0, 24).map((x, n) => (
            <button key={x.remittance_line_id} onClick={() => setI(n)}
                    className={`h-2 w-2 rounded-full ${n === i % queue.length ? "bg-forest" : "bg-rule hover:bg-leaf"}`} />
          ))}
        </span>
        <label className="ml-auto flex items-center gap-2 text-muted">
          Reviewing as
          <input value={reviewer} onChange={(e) => setReviewer(e.target.value)} placeholder="your name"
                 className="w-32 rounded-full border border-rule bg-sheet px-3 py-1 text-ink focus:border-forest focus:outline-none" />
        </label>
      </div>

      <div className="relative">
      {queue.slice(i % queue.length + 1, i % queue.length + 3).map((x, n) => (
        <div key={x.remittance_line_id} aria-hidden className="absolute inset-x-0 top-0 h-full rounded-2xl border border-rule bg-sheet"
             style={{ transform: `translateY(${(n + 1) * 10}px) scale(${1 - (n + 1) * 0.03})`, zIndex: -1 - n, opacity: 0.7 - n * 0.25 }} />
      ))}
      <div key={card.remittance_line_id} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}
           style={dx ? { transform: `translateX(${dx}px) rotate(${dx / 25}deg)`, transition: "none",
                         boxShadow: `0 0 0 ${Math.min(6, Math.abs(dx) / 30)}px ${dx > 0 ? "#609D7B" : "#B3402F"}` } : undefined}
           className={`relative touch-pan-y select-none rounded-2xl border border-rule bg-sheet p-6 shadow-[0_1px_0_#E4E1D8] transition duration-300 ${dx ? "cursor-grabbing" : "cursor-grab"} ${
             leaving === "r" ? "translate-x-24 rotate-2 opacity-0" : leaving === "l" ? "-translate-x-24 -rotate-2 opacity-0" : "animate-rise"}`}>
        {Math.abs(dx) > 30 ? (
          <div className={`pointer-events-none absolute top-6 ${dx > 0 ? "left-6" : "right-6"} -rotate-6 rounded-xl border-4 px-3 py-1 text-xl font-black tracking-widest`}
               style={{ color: dx > 0 ? "#28543E" : "#B3402F", borderColor: dx > 0 ? "#28543E" : "#B3402F", opacity: Math.min(1, Math.abs(dx) / 140) }}>
            {dx > 0 ? "POST IT" : "WRONG"}</div>) : null}
        <div className="grid items-center gap-6 md:grid-cols-[1fr_auto_1fr]">
          <div>
            <p className="text-sm text-muted">Payment {card.payment_reference} · {card.payment_date}</p>
            <p className="mt-1 font-serif text-4xl tabular-nums">{money(card.amount)}</p>
            <p className="mt-2 text-sm">Remittance says <span className="rounded bg-paper px-1.5 py-0.5 font-mono">{card.raw_invoice_ref ?? "nothing"}</span></p>
          </div>
          <div className="flex flex-col items-center">
            <svg viewBox="0 0 120 8" className="hidden h-2 w-28 md:block" aria-hidden>
              <line x1="0" y1="4" x2="120" y2="4" stroke="#609D7B" strokeWidth="2" strokeDasharray="6 6" className="animate-flow" />
            </svg>
            <Ring value={conf} />
          </div>
          <div className="md:text-right">
            <p className="text-sm text-muted">Invoice {card.suggested_invoice}{card.invoice_date ? ` · ${card.invoice_date}` : ""}</p>
            <p className="mt-1 font-serif text-4xl tabular-nums">{card.invoice_amount ? money(card.invoice_amount) : "?"}</p>
            <p className={`mt-2 text-sm ${gap === 0n ? "text-credit" : "text-debit"}`}>
              {gap === 0n ? "Amounts match to the cent" : gap < 0n ? `Short by ${money(fromCents(-gap))}` : `Over by ${money(fromCents(gap))}`}
            </p>
          </div>
        </div>
        <div className="mt-5 grid gap-5 md:grid-cols-2">
          <RefTiles paid={card.raw_invoice_ref} invoice={card.suggested_invoice} />
          <AmountBars paid={Number(card.amount)} billed={card.invoice_amount ? Number(card.invoice_amount) : null} />
        </div>
        <div className="mt-5 flex flex-wrap gap-2">
          {chips(card.explanation).map((c) => (
            <span key={c} className="rounded-full border border-rule bg-paper px-3 py-1 text-sm">{c}</span>
          ))}
        </div>
        <div className="mt-6 flex flex-wrap items-center gap-3">
          <button onClick={() => act("approve")} disabled={pending}
                  className="rounded-full bg-forest px-6 py-2.5 font-semibold text-white hover:bg-ink disabled:opacity-50">
            Approve and post <kbd className="ml-1 rounded bg-white/20 px-1.5 text-xs">A</kbd>
          </button>
          <button onClick={() => act("reject")} disabled={pending}
                  className="rounded-full border border-debit px-6 py-2.5 font-semibold text-debit hover:bg-debit hover:text-white disabled:opacity-50">
            Wrong invoice <kbd className="ml-1 rounded border border-current px-1.5 text-xs">R</kbd>
          </button>
          <button onClick={() => setI((x) => x + 1)} className="px-3 py-2.5 text-muted hover:text-ink">Skip →</button>
          {err ? <span role="alert" className="text-sm font-semibold text-debit">{err}</span> : null}
          {done.length ? <span className="ml-auto text-sm text-muted">{approved} approved · {done.length - approved} rejected</span> : null}
        </div>
        <p className="mt-3 text-xs text-muted">Drag the card: right to post, left for wrong invoice.</p>
      </div>
      </div>
    </div>
  );
}
