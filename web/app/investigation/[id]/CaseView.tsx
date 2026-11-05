"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import { money } from "@/lib/format";
import type { CaseDetail, ToolCall } from "@/lib/types";
import { useReviewer } from "@/components/useReviewer";
import { ACTION, cap } from "../CaseQueue";
import { decideCase } from "./actions";

const TOOL: Record<string, string> = {
  claim: "Read the claim", engine_assessment: "Read the engine's check", promotions: "Looked up promotions on file",
  pos_weeks: "Read the retailer's POS", shelf_prices: "Checked shelf prices", accepted_against: "Checked earlier claims",
  record: "Recorded the arithmetic",
};

type Drop = { week: string; price: string; prior_high: string };
function isDrops(v: unknown): v is Drop[] {
  return Array.isArray(v) && v.length > 0 && typeof v[0] === "object" && v[0] !== null && "prior_high" in (v[0] as object);
}

function Drops({ v }: { v: Drop[] }) {
  // The price evidence in one line, then one chip per week: what a reviewer needs to see, not a dump.
  const low = Math.min(...v.map((d) => Number(d.price))), high = Math.max(...v.map((d) => Number(d.prior_high)));
  const off = Math.round((1 - low / high) * 100);
  return (
    <div>
      <p className="text-sm font-semibold">{v.length} week{v.length === 1 ? "" : "s"} at ${low.toFixed(2)}, usually ${high.toFixed(2)}: {off}% below</p>
      <div className="mt-2 flex flex-wrap gap-1">
        {v.map((d) => (
          <span key={d.week} className="rounded bg-sheet px-1.5 py-0.5 text-[11px] tabular-nums" title={`$${Number(d.price).toFixed(2)} vs prior high $${Number(d.prior_high).toFixed(2)}`}>{d.week}</span>
        ))}
      </div>
    </div>
  );
}

function show(v: unknown): string {
  if (Array.isArray(v)) return v.length && typeof v[0] === "object"
    ? v.map((x) => Object.entries(x as Record<string, unknown>).map(([k, y]) => `${k.replace(/_/g, " ")} ${y}`).join(", ")).join("; ")
    : v.join(", ");
  if (v && typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function CaseView({ c }: { c: CaseDetail }) {
  const steps = c.finding.tool_calls;
  const [step, setStep] = useState<number | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const play = () => {
    if (timer.current) clearInterval(timer.current);
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { setStep(null); return; }
    setStep(0);
    timer.current = setInterval(() => setStep((s) => {
      if (s === null || s >= steps.length - 1) { if (timer.current) clearInterval(timer.current); return null; }
      return s + 1;
    }), 380);
  };
  useEffect(() => { play(); return () => { if (timer.current) clearInterval(timer.current); }; }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  const visible = step === null ? steps.length : step + 1;
  const done = step === null;
  const a = ACTION[c.agent_action];

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm text-muted">{c.code} · {c.raw_reason} · taken {c.deduction_date}</p>
          <p className={`mt-1 inline-block rounded-full px-3 py-0.5 text-xs font-semibold ${c.window_open ? "bg-forest text-white" : "bg-debit/10 text-debit"}`}>
            {c.window_open ? `Dispute window open until ${c.dispute_deadline}` : `Dispute window closed on ${c.dispute_deadline} (ledger date ${c.as_of}): recovery now depends on goodwill`}
          </p>
          <h1 className="font-serif text-5xl font-light">Claim {c.claim_number}</h1>
        </div>
        <div className="text-right">
          <div className="font-serif text-4xl tabular-nums">{money(c.amount)}</div>
          <div className="text-sm text-muted">taken from your invoice</div>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <section aria-label="Investigation">
          <div className="mb-3 flex items-center gap-3">
            <button onClick={play} className="rounded-full bg-ink px-4 py-2 text-sm font-bold text-paper hover:bg-forest">
              {done ? "Replay the investigation" : "Investigating…"}
            </button>
            <span className="text-sm text-muted">{steps.length} steps · {c.agent_version}</span>
          </div>
          <ol className="relative space-y-2 border-l-2 border-rule pl-5">
            {steps.slice(0, visible).map((t: ToolCall, i) => (
              <li key={i} className="animate-rise">
                <span className={`absolute -left-[7px] mt-1.5 h-3 w-3 rounded-full ${i === visible - 1 && !done ? "bg-tag ring-4 ring-tag/40" : "bg-forest"}`} />
                <div className="rounded-xl border border-rule bg-sheet px-4 py-2.5">
                  <span className="font-semibold">{TOOL[t.tool] ?? t.tool}</span>
                  <span className="ml-2 text-sm text-muted">
                    {t.tool === "record" ? `calculation #${t.calculation_id}` : `${t.rows ?? 0} record${t.rows === 1 ? "" : "s"}`}
                  </span>
                </div>
              </li>
            ))}
          </ol>
          {done ? (
            <div className="mt-5 animate-rise">
              <h2 className="mb-2 text-xl">What it found</h2>
              <dl className="space-y-2">
                {c.finding.facts.map((f, i) => (
                  <div key={i} className="rounded-xl bg-paper px-4 py-2.5">
                    <dt className="text-xs text-muted">{f.fact}</dt>
                    <dd className="text-sm font-semibold">{isDrops(f.value) ? <Drops v={f.value} /> : show(f.value)}</dd>
                  </div>
                ))}
              </dl>
              {c.calculations.map((k) => (
                <details key={k.calculation_id} className="mt-3 rounded-xl border border-rule bg-sheet px-4 py-3">
                  <summary className="cursor-pointer">
                    <span className="font-semibold">Calculation #{k.calculation_id}</span>
                    <span className="ml-2 text-sm text-muted">{k.kind.replace(/_/g, " ")} = </span>
                    <span className="font-serif text-lg tabular-nums">{money(String(k.result))}</span>
                  </summary>
                  <pre className="mt-2 max-h-56 overflow-auto rounded bg-paper p-3 text-xs">{JSON.stringify(k.inputs, null, 1)}</pre>
                  <p className="mt-1 text-xs text-muted">computed by {k.computed_by}, {k.formula_version}; recomputable from these inputs</p>
                </details>
              ))}
            </div>
          ) : null}
        </section>

        <section aria-label="Recommendation" className={done ? "animate-rise" : "opacity-30 transition-opacity"}>
          <div className="rounded-2xl border border-rule bg-sheet p-5">
            <div className="flex flex-wrap items-center gap-3">
              <span className={`rounded-full px-4 py-1.5 font-bold ${a.cls}`}>{a.label}</span>
              {c.agent_action === "dispute" && c.recoverable ? <span className="font-serif text-2xl tabular-nums text-credit">{money(c.recoverable)} back</span> : null}
            </div>
            <p className="mt-3 text-lg">{cap(c.agent_reason)}</p>
            {c.agent_request ? <p className="mt-2 text-sm"><b>Document:</b> {c.agent_request}</p> : null}
            <div className="mt-4 grid grid-cols-2 gap-2 text-sm">
              <div className="rounded-lg bg-paper p-3"><div className="text-xs text-muted">Engine alone</div>{c.engine_verdict.replace("_", " ")} <span className="text-muted">({c.engine_rule.replace(/_/g, " ")})</span></div>
              <div className={`rounded-lg p-3 ${c.differs_from_engine ? "bg-tag" : "bg-paper"}`}><div className="text-xs text-muted">Agent</div>{a.label}{c.differs_from_engine ? " · differs" : ""}</div>
            </div>
          </div>
          <Letter c={c} />
          <Decision c={c} />
        </section>
      </div>
    </div>
  );
}

function Letter({ c }: { c: CaseDetail }) {
  const parts = c.letter.text.split(/(\$[\d,]+\.\d{2}|\b\d{4}-\d{2}-\d{2}\b)/g);
  return (
    <div className="mt-4 rounded-2xl border border-rule bg-[#FFFEFB] p-5 shadow-[0_8px_30px_rgba(31,42,36,0.06)]">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs">
        <span className="rounded bg-paper px-2 py-1 font-semibold">
          Draft, not sent · {c.letter.source === "model" ? `written by ${c.letter.model ?? "a model"}` : "template"}
        </span>
        <span className={c.letter.unsupported.length ? "font-semibold text-debit" : "text-credit"}>
          {c.letter.unsupported.length ? `Blocked: ${c.letter.unsupported.join(", ")} has no source` : "Every number checked against the case's facts and calculations"}
        </span>
      </div>
      {c.letter.model_rejected ? (
        <p className="mb-3 rounded-lg bg-debit/10 px-3 py-2 text-xs text-debit">
          The {c.letter.model ?? "model"} draft was rejected:{" "}
          {[c.letter.model_rejected.unsupported_numbers.length ? `${c.letter.model_rejected.unsupported_numbers.join(", ")} has no source on this case` : "",
            ...(c.letter.model_rejected.window_problems ?? [])].filter(Boolean).join("; ")}. Showing the template instead.
        </p>
      ) : null}
      <pre className="whitespace-pre-wrap font-sans text-sm leading-6">
        {parts.map((p, i) => /^\$|^\d{4}-/.test(p)
          ? <mark key={i} title="traced to a fact or calculation on this case" className="rounded bg-leaf/20 px-0.5 text-ink">{p}</mark>
          : <span key={i}>{p}</span>)}
      </pre>
    </div>
  );
}

function Decision({ c }: { c: CaseDetail }) {
  const [reviewer, setReviewer] = useReviewer();
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [pending, start] = useTransition();
  const closed = c.status === "closed" || !!c.human_action && c.status !== "awaiting_evidence";
  const go = (action: string) => start(async () => {
    const r = await decideCase(c.case_id, action, reviewer, note);
    setMsg(r.ok ? { ok: true, text: `Recorded: ${r.decision.replace("_", " ")}. Claim is now ${r.deduction_status.replace("_", " ")}.` }
                : { ok: false, text: r.error });
  });
  if (closed) {
    const last = [...c.trail].reverse().find((t) => t.actor_type === "human");
    return <p className="mt-4 rounded-full bg-paper px-4 py-2 text-center font-semibold">Decided: {last?.action.replace("case_", "").replace("_", " ")} by {last?.actor_id}</p>;
  }
  const primary = c.agent_action === "request" ? ["request_sent", "Mark the request as sent"]
    : ["approve", c.agent_action === "dispute" ? "Approve the dispute" : "Approve: accept the claim"];
  const other = c.agent_action === "dispute" ? ["accept", "Accept instead"] : ["dispute", "Dispute instead"];
  return (
    <div className="mt-4 rounded-2xl border border-rule bg-sheet p-5">
      <div className="grid gap-2 sm:grid-cols-2">
        <input value={reviewer} onChange={(e) => setReviewer(e.target.value)} placeholder="Your name"
               className="rounded-full border border-rule px-4 py-2 focus:border-forest focus:outline-none" />
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)"
               className="rounded-full border border-rule px-4 py-2 focus:border-forest focus:outline-none" />
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <button disabled={pending} onClick={() => go(primary[0])} className="rounded-full bg-forest px-5 py-2.5 font-semibold text-white hover:bg-ink disabled:opacity-50">{primary[1]}</button>
        <button disabled={pending} onClick={() => go(other[0])} className="rounded-full border border-forest px-5 py-2.5 font-semibold text-forest hover:bg-paper disabled:opacity-50">{other[1]}</button>
        {c.agent_action === "request" ? <button disabled={pending} onClick={() => go("accept")} className="rounded-full border border-forest px-5 py-2.5 font-semibold text-forest hover:bg-paper disabled:opacity-50">Accept instead</button> : null}
      </div>
      {msg ? <p role="status" className={`mt-2 text-sm ${msg.ok ? "text-credit" : "font-semibold text-debit"}`}>{msg.text}</p> : null}
      <p className="mt-2 text-xs text-muted">Your decision is recorded on the case, with whether you followed the agent.</p>
    </div>
  );
}
