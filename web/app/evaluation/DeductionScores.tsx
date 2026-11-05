"use client";

import { useState } from "react";
import type { Phase2aReport, RegionScore } from "@/lib/types";

type Key = "report_region" | "design_region";

function Big({ v, label, bad = false }: { v: string; label: string; bad?: boolean }) {
  return (
    <div className="px-5 py-4 first:pl-0">
      <div className={"font-figure text-5xl leading-none tabular-nums " + (bad ? "text-debit" : "")}>{v}</div>
      <div className="mt-2 text-sm text-muted">{label}</div>
    </div>
  );
}

// Tuned on one region, reported on the other: the switch shows both, the report region first.
export function DeductionScores({ d }: { d: Phase2aReport["deductions"] }) {
  const [k, setK] = useState<Key>("report_region");
  const r: RegionScore = d[k];
  const mistakes = d.mistakes.filter((m) => m.region && r.regions.includes(m.region));
  return (
    <div>
      <div role="tablist" className="mb-4 inline-flex border border-ink text-sm">
        {(["report_region", "design_region"] as Key[]).map((key) => (
          <button key={key} role="tab" aria-selected={k === key} onClick={() => setK(key)}
                  className={"px-3 py-1 " + (k === key ? "bg-ink text-paper" : "hover:bg-sheet")}>
            {key === "report_region" ? "Reported" : "Tuned on"} · {d[key].regions.join(" + ")}
          </button>
        ))}
      </div>
      <div className="mb-4 flex flex-wrap divide-x divide-rule border-y border-rule">
        <Big v={`${r.false_approval.k}/${r.false_approval.n}`} label="Bad claims approved" bad={r.false_approval.k > 0} />
        <Big v={`${r.false_dispute.k}/${r.false_dispute.n}`} label="Good claims disputed" bad={r.false_dispute.k > 0} />
        <Big v={`${r.needs_evidence.k}`} label="Sent for evidence" />
        <Big v={`${r.recoverable_exact_to_the_cent_when_decided.k}/${r.recoverable_exact_to_the_cent_when_decided.n}`}
             label="Exact to the cent" />
      </div>
      {mistakes.length ? (
        <ul className="text-sm">
          {mistakes.map((m) => (
            <li key={m.claim} className="border-l-4 border-debit bg-sheet px-3 py-1.5 mb-1">
              <span className="font-semibold">{m.claim}</span> · {m.rule.replace(/_/g, " ")} · truly {m.truth.replace(/_/g, " ")} · {m.region}
            </li>
          ))}
        </ul>
      ) : <p className="text-sm text-muted">No mistakes in this region.</p>}
    </div>
  );
}
