import Link from "next/link";
import { count } from "@/lib/format";
import type { RegressionGate } from "@/lib/types";

// Every value a person confirmed, replayed against the current rule. Green only when none is broken.
export function Gate({ g }: { g: RegressionGate }) {
  if (g.confirmed_values === 0 && g.not_replayable_yet === 0) return null;
  const parts = [
    { n: g.kept_right, cls: "bg-leaf", label: "still accepted right" },
    { n: g.asks_a_person, cls: "bg-tag", label: "sent to a person" },
    { n: g.open_mistakes, cls: "bg-[#D9822B]", label: "known mistake, not fixed yet" },
    { n: g.regressions, cls: "bg-debit", label: "broken by this rule" },
  ];
  const total = Math.max(1, g.confirmed_values);
  return (
    <section aria-label="Regression gate" className={`mb-8 rounded-2xl border p-5 ${g.passed ? "border-rule bg-sheet" : "border-debit bg-debit/5"}`}>
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className={`rounded-full px-3 py-1 text-sm font-bold ${g.passed ? "bg-forest text-white" : "bg-debit text-white"}`}>
          {g.passed ? "Gate passes" : "Gate fails"}
        </span>
        <span className="font-serif text-2xl">{count(g.confirmed_values)} values people confirmed</span>
        <span className="text-sm text-muted">replayed on rule {g.policy.replace("two-readers-", "")}, no AI calls</span>
      </div>
      <div className="mt-4 flex h-3 overflow-hidden rounded-full bg-rule">
        {parts.map((p, i) => p.n > 0 ? <div key={p.label} className={`${p.cls} origin-left animate-grow`}
          style={{ width: `${(100 * p.n) / total}%`, animationDelay: `${i * 150}ms` }} /> : null)}
      </div>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-sm">
        {parts.map((p) => (
          <span key={p.label} className="flex items-center gap-2">
            <span className={`h-2.5 w-2.5 rounded-full ${p.cls}`} /><b className="tabular-nums">{count(p.n)}</b>
            <span className="text-muted">{p.label}</span>
          </span>
        ))}
      </div>
      {g.wrong.length ? (
        <ul className="mt-3 flex flex-wrap gap-2 text-sm">
          {g.wrong.slice(0, 8).map((w) => (
            <li key={`${w.doc}-${w.field}`}>
              <Link href={`/documents/${w.doc}`} className={`inline-block rounded-full border px-3 py-1 hover:bg-paper ${w.kind === "regression" ? "border-debit text-debit" : "border-rule"}`}>
                #{w.doc} {w.field.replace(/_/g, " ")}: {w.now_accepts ?? "absent"}, should be {w.truth ?? "absent"}
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
      {g.not_replayable_yet ? (
        <p className="mt-3 text-xs text-muted">{count(g.not_replayable_yet)} reviewed fields were decided before inputs were stored. Run make decide-docs ROUND=5 (replays, costs nothing).</p>
      ) : null}
    </section>
  );
}
