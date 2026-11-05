"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { count, money } from "@/lib/format";
import type { CaseRow } from "@/lib/types";

export const cap = (t: string) => (t ? t[0].toUpperCase() + t.slice(1) : t);

export const ACTION: Record<string, { label: string; cls: string }> = {
  dispute: { label: "Dispute", cls: "bg-debit text-white" },
  request: { label: "Ask for a document", cls: "bg-tag text-ink" },
  accept: { label: "Accept", cls: "bg-leaf/25 text-forest" },
};

type Filter = "attention" | "dispute" | "request" | "accept" | "decided";

export function CaseQueue({ rows }: { rows: CaseRow[] }) {
  const open = rows.filter((r) => !r.human_action);
  const sets: Record<Filter, CaseRow[]> = {
    attention: open.filter((r) => r.differs_from_engine),
    dispute: open.filter((r) => r.agent_action === "dispute"),
    request: open.filter((r) => r.agent_action === "request"),
    accept: open.filter((r) => r.agent_action === "accept"),
    decided: rows.filter((r) => r.human_action),
  };
  const [f, setF] = useState<Filter>(sets.attention.length ? "attention" : "dispute");
  const [shown, setShown] = useState(12);
  const list = useMemo(() => sets[f], [f, rows]);   // eslint-disable-line react-hooks/exhaustive-deps
  const tabs: [Filter, string][] = [["attention", "Agent and engine differ"], ["dispute", "Disputes"],
    ["request", "Document requests"], ["accept", "Accepted"], ["decided", "Decided by you"]];
  return (
    <div>
      <div className="mb-6 grid gap-3 sm:grid-cols-3">
        <Stat n={rows.length} label="claims investigated" />
        <Stat n={sets.attention.length} label="where the agent and the engine differ" hot />
        <Stat n={open.filter((r) => r.window_open && r.agent_action !== "accept").length} label="still recoverable: dispute window open" />
      </div>
      <div className="mb-4 flex flex-wrap gap-2">
        {tabs.map(([k, l]) => (
          <button key={k} onClick={() => { setF(k); setShown(12); }} aria-pressed={f === k}
                  className={`rounded-full border px-3 py-1.5 text-sm transition ${f === k ? "border-forest bg-forest text-white" : "border-rule bg-sheet hover:border-forest"}`}>
            {l} <span className="ml-1 opacity-70">{count(sets[k].length)}</span>
          </button>
        ))}
      </div>
      <ul className="divide-y divide-rule overflow-hidden rounded-2xl border border-rule bg-sheet">
        {list.slice(0, shown).map((r, i) => (
          <li key={r.case_id} className="animate-rise" style={{ animationDelay: `${Math.min(i, 12) * 25}ms` }}>
            <Link href={`/investigation/${r.case_id}`}
                  className="grid grid-cols-[1fr_auto] items-center gap-4 px-5 py-4 hover:bg-paper md:grid-cols-[9rem_1fr_8rem_11rem]">
              <span>
                <span className="font-semibold">{r.claim_number}</span>
                <span className="block text-xs text-muted">{r.code} · {r.raw_reason}</span>
                <span className={`mt-1 inline-block rounded px-1.5 text-[11px] ${r.window_open ? "bg-forest text-white" : "bg-paper text-muted"}`}>
                  {r.window_open ? `window open to ${r.dispute_deadline}` : "window closed"}
                </span>
              </span>
              <span className="hidden text-sm md:block">
                {r.differs_from_engine ? <span className="mr-2 rounded bg-tag px-1.5 py-0.5 text-xs font-bold">engine said {r.engine_verdict.replace("_", " ")}</span> : null}
                {r.agent_action === "request" && r.agent_request ? <>Missing: {r.agent_request}</> : cap(r.agent_reason)}
              </span>
              <span className="text-right font-serif text-xl tabular-nums">{money(r.amount)}</span>
              <span className="hidden text-right md:block">
                {r.human_action
                  ? <span className="rounded-full bg-paper px-3 py-1 text-xs">{r.human_action.replace("case_", "").replace("_", " ")} · {r.reviewer}</span>
                  : <span className={`rounded-full px-3 py-1 text-xs font-semibold ${ACTION[r.agent_action].cls}`}>{ACTION[r.agent_action].label}</span>}
              </span>
            </Link>
          </li>
        ))}
        {list.length === 0 ? <li className="p-6 text-center text-muted">Nothing here.</li> : null}
      </ul>
      {list.length > shown ? (
        <button onClick={() => setShown((s) => s + 12)} className="mt-3 rounded-full border border-rule px-4 py-2 text-sm hover:border-forest">
          Show 12 more of {count(list.length - shown)}
        </button>
      ) : null}
    </div>
  );
}

function Stat({ n, label, hot = false }: { n: number; label: string; hot?: boolean }) {
  return (
    <div className={`rounded-2xl border p-5 ${hot ? "border-ink bg-tag" : "border-rule bg-sheet"}`}>
      <div className="font-serif text-5xl font-light leading-none tabular-nums">{count(n)}</div>
      <div className="mt-2 text-sm">{label}</div>
    </div>
  );
}
