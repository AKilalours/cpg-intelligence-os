"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { getRecent, getRole, ROLES, setRole, type Role, type RoleId, type Visit } from "@/lib/journey";

function useJourney() {
  const [role, setR] = useState<Role | null>(null);
  const [recent, setRecent] = useState<Visit[]>([]);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const sync = () => { setR(getRole()); setRecent(getRecent()); setReady(true); };
    sync();
    window.addEventListener("cpgos:journey", sync);
    window.addEventListener("storage", sync);
    return () => { window.removeEventListener("cpgos:journey", sync); window.removeEventListener("storage", sync); };
  }, []);
  return { role, recent, ready };
}

const ICON: Record<RoleId, string> = {
  deductions: "M4 7h16M4 12h10M4 17h6M17 14l3 3-3 3",
  planning: "M4 18l5-6 4 3 7-9M15 6h5v5",
  leadership: "M5 19V9M10 19V5M15 19v-7M20 19v-4",
};

function Picker({ onClose, current }: { onClose: () => void; current: Role | null }) {
  const router = useRouter();
  const choose = (r: Role) => { setRole(r.id); onClose(); router.push(r.start); };
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-ink/40 p-4 backdrop-blur-sm md:items-center" role="dialog" aria-modal="true" aria-label="Choose your workspace">
      <div className="w-full max-w-3xl animate-rise rounded-3xl bg-paper p-6 shadow-2xl md:p-8">
        <div className="font-serif text-4xl font-light">Choose your workspace</div>
        <p className="mt-1 text-muted">Each workspace opens on its own work and suggests what to do next. Switch at any time.</p>
        <div className="mt-6 grid gap-3 md:grid-cols-3">
          {ROLES.map((r) => (
            <button key={r.id} onClick={() => choose(r)} autoFocus={r.id === (current?.id ?? "deductions")}
                    className={`lift group rounded-2xl border bg-sheet p-5 text-left ${current?.id === r.id ? "border-forest ring-2 ring-forest" : "border-rule"}`}>
              <svg viewBox="0 0 24 24" className="h-9 w-9 rounded-xl bg-forest p-1.5 text-tag" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={ICON[r.id]} /></svg>
              <div className="mt-4 text-lg font-bold">{r.title}</div>
              <div className="text-sm text-muted">{r.who}</div>
              <div className="mt-4 rounded-xl bg-paper px-3 py-2 text-sm group-hover:bg-tag">First: {r.first}</div>
            </button>
          ))}
        </div>
        {current ? <button onClick={onClose} className="mt-5 text-sm text-muted underline underline-offset-2">Keep {current.title}</button> : null}
      </div>
    </div>
  );
}

// In the nav: the current workspace, and the picker on first visit.
export function WorkspaceChip() {
  const { role, ready } = useJourney();
  const [open, setOpen] = useState(false);
  useEffect(() => { if (ready && !role) setOpen(true); }, [ready, role]);
  return (
    <>
      <button onClick={() => setOpen(true)} className="ml-auto shrink-0 rounded-lg border border-rule px-3 py-1.5 text-sm hover:bg-paper" aria-haspopup="dialog">
        {role ? role.title : "Choose workspace"}
      </button>
      {/* Portal to <body>: the nav's backdrop blur makes it the containing block for fixed children, which
          clipped the picker to the height of the nav bar. */}
      {open ? createPortal(<Picker current={role} onClose={() => setOpen(false)} />, document.body) : null}
    </>
  );
}

export interface Step { href: string; title: string; detail?: string; roles?: RoleId[] }

// Two or three suggestions for what to do next, the visitor's own workspace first.
export function NextSteps({ steps, heading = "Next" }: { steps: Step[]; heading?: string }) {
  const { role } = useJourney();
  const ordered = [...steps].sort((a, b) => Number(!!role && !!b.roles?.includes(role.id)) - Number(!!role && !!a.roles?.includes(role.id)));
  if (!ordered.length) return null;
  return (
    <section aria-label="Suggested next steps" className="mt-10">
      <div className="mb-3 text-sm font-bold text-muted">{heading}</div>
      <div className="grid gap-3 md:grid-cols-3">
        {ordered.slice(0, 3).map((s, i) => (
          <Link key={s.href + s.title} href={s.href}
                className={`lift flex items-start gap-3 rounded-2xl border p-4 ${i === 0 ? "border-forest bg-forest text-paper" : "border-rule bg-sheet"}`}>
            <span className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${i === 0 ? "bg-tag text-ink" : "bg-paper"}`} aria-hidden="true">
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M9 6l6 6-6 6" /></svg>
            </span>
            <span><span className="block font-bold">{s.title}</span>{s.detail ? <span className={`block text-sm ${i === 0 ? "text-paper/80" : "text-muted"}`}>{s.detail}</span> : null}</span>
          </Link>
        ))}
      </div>
    </section>
  );
}

// Where the visitor has been, so they can pick up where they left off.
export function RecentStrip() {
  const { recent } = useJourney();
  if (!recent.length) return null;
  return (
    <section aria-label="Recently viewed" className="mb-8">
      <div className="mb-2 text-sm font-bold text-muted">Pick up where you left off</div>
      <div className="flex gap-2 overflow-x-auto pb-1">
        {recent.map((v) => (
          <Link key={v.href} href={v.href} className="lift shrink-0 rounded-xl border border-rule bg-sheet px-3 py-2 text-sm">
            <span className="block text-xs text-muted">{v.kind}</span><span className="font-bold">{v.label}</span>
          </Link>
        ))}
      </div>
    </section>
  );
}

// A short message that says what just happened, then goes away.
export function Toast({ text, onDone }: { text: string | null; onDone: () => void }) {
  useEffect(() => { if (!text) return; const t = setTimeout(onDone, 4200); return () => clearTimeout(t); }, [text, onDone]);
  if (!text) return null;
  return (
    <div role="status" className="fixed bottom-5 left-1/2 z-40 -translate-x-1/2 animate-rise rounded-2xl bg-ink px-4 py-3 text-sm text-paper shadow-xl">{text}</div>
  );
}
