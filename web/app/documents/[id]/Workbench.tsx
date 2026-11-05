"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DocumentView, FieldDecision, Reading } from "@/lib/types";
import { ReviewForm } from "./ReviewForm";

const READER: Record<string, string> = { rules: "OCR", local_model: "Local AI", claude: "Claude" };
const label = (f: string) => f.replace(/_/g, " ");
type Box = [number, number, number, number];

// The replay is a timeline: each reader's located values land on the page in turn, then the rule decides.
type Step = { kind: "read"; r: Reading } | { kind: "decide" };

function timeline(readings: Reading[]): Step[] {
  const steps: Step[] = readings.filter((r) => r.bbox).map((r) => ({ kind: "read", r }));
  return [...steps, { kind: "decide" }];
}

export function Workbench({ doc, nextId }: { doc: DocumentView; nextId: number | null }) {
  const fields = useMemo(() => {
    const waiting = doc.fields.filter((f) => f.status === "review" && !f.review);
    const rest = doc.fields.filter((f) => !(f.status === "review" && !f.review) && (f.value !== null || f.review));
    return [...waiting, ...rest];
  }, [doc.fields]);
  const steps = useMemo(() => timeline(doc.readings), [doc.readings]);
  const [hover, setHover] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(fields.find((f) => f.status === "review" && !f.review)?.field ?? null);
  const [step, setStep] = useState<number | null>(null);    // null: show the decided page
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const stop = useCallback(() => { if (timer.current) clearInterval(timer.current); timer.current = null; }, []);
  const play = useCallback(() => {
    stop();
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { setStep(null); return; }
    setStep(0);
    timer.current = setInterval(() => {
      setStep((s) => {
        if (s === null || s >= steps.length - 1) { stop(); return null; }
        return s + 1;
      });
    }, 110);
  }, [steps.length, stop]);
  useEffect(() => { play(); return stop; }, [play, stop]);   // the page opens with the replay

  // Keyboard: j / k move between fields, r replays.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest("input, textarea")) return;
      const i = fields.findIndex((f) => f.field === open);
      if (e.key === "j") setOpen(fields[Math.min(fields.length - 1, i + 1)]?.field ?? null);
      if (e.key === "k") setOpen(fields[Math.max(0, i - 1)]?.field ?? null);
      if (e.key === "r") play();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fields, open, play]);

  const playing = step !== null;
  const shown = playing ? steps.slice(0, step + 1) : steps;
  const current = playing ? steps[step] : null;
  const decided = !playing;
  const accepted = doc.fields.filter((f) => f.status === "accepted").length;
  const caption = !current ? null : current.kind === "decide" ? "Rule decides" : `${READER[current.r.reader]} reading ${label(current.r.field)}`;

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,6fr)_minmax(0,5fr)]">
      <section aria-label="Invoice page">
        <div className="mb-3 flex items-center gap-3">
          <button onClick={play} className="rounded-lg bg-ink px-4 py-2 text-sm font-bold text-paper hover:bg-forest">
            {playing ? "Reading…" : "Watch it read"}
          </button>
          <span className="text-sm text-muted" aria-live="polite">{caption ?? `${accepted} accepted · ${doc.open_fields} for you`}</span>
        </div>
        <div className="relative overflow-hidden rounded-2xl border border-rule bg-sheet shadow-[0_8px_30px_rgba(31,42,36,0.08)]">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={`/documents/${doc.source_document_id}/image`} alt={`Invoice ${doc.source_document_id}`} className="block w-full" />
          {playing ? <div className="pointer-events-none absolute inset-x-0 h-0.5 animate-sweep bg-sky/60" /> : null}
          {shown.map((s, i) => s.kind === "read" && s.r.bbox ? (
            <Mark key={`r${i}`} box={s.r.bbox} cls={decided ? "border-transparent" :
              s.r.reader === "rules" ? "border-ink/60" : "border-sky"} land />
          ) : null)}
          {decided ? fields.map((f) => f.bbox ? (
            <button key={f.field} onClick={() => setOpen(f.field)} onMouseEnter={() => setHover(f.field)} onMouseLeave={() => setHover(null)}
                    aria-label={`${label(f.field)}: ${f.value ?? "no value"}`}
                    className={"absolute rounded-sm border-2 transition " + tone(f) + (hover === f.field || open === f.field ? " ring-4 ring-tag" : "")}
                    style={pos(f.bbox)} />
          ) : null) : null}
        </div>
        <p className="mt-2 text-xs text-muted">j / k next field · r replay</p>
      </section>

      <section aria-label="Fields">
        {doc.open_fields === 0 ? (
          <div className="mb-4 flex items-center justify-between rounded-xl bg-forest px-4 py-3 text-paper">
            <span className="font-bold">All fields checked</span>
            {nextId ? <Link href={`/documents/${nextId}`} className="rounded-lg bg-tag px-3 py-1.5 font-bold text-ink">Next invoice</Link> : null}
          </div>
        ) : null}
        <ul className="space-y-2">
          {fields.map((f) => {
            const isOpen = open === f.field;
            const waiting = f.status === "review" && !f.review;
            return (
              <li key={f.field} onMouseEnter={() => setHover(f.field)} onMouseLeave={() => setHover(null)}
                  className={"rounded-xl border bg-sheet transition " + (isOpen ? "border-forest" : "border-rule") + (hover === f.field ? " shadow-[0_4px_16px_rgba(31,42,36,0.08)]" : "")}>
                <button onClick={() => setOpen(isOpen ? null : f.field)} aria-expanded={isOpen}
                        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left">
                  <span>
                    <span className="block text-xs text-muted">{label(f.field)}</span>
                    <span className="font-serif text-xl tabular-nums">{f.review ? (f.review.value ?? "not on page") : (f.value ?? "?")}</span>
                  </span>
                  <Pill f={f} />
                </button>
                {isOpen ? (
                  <div className="border-t border-rule px-4 pb-4 pt-3 text-sm">
                    <div className="mb-3 flex flex-wrap gap-2">
                      {Object.entries(f.candidates).map(([k, v]) => (
                        <span key={k} className="rounded-md bg-paper px-2 py-1"><span className="text-muted">{READER[k] ?? k}</span> {v}</span>
                      ))}
                      {Object.keys(f.candidates).length === 0 ? <span className="text-muted">No reader found a value</span> : null}
                    </div>
                    <p className="mb-3 text-muted">{f.reason}</p>
                    {waiting ? <ReviewForm documentId={doc.source_document_id} field={f.field} machineValue={f.value ?? Object.values(f.candidates)[0] ?? null} /> : null}
                    {f.review ? <p className="text-muted">{f.review.action === "approve" ? "Approved" : "Corrected"} by {f.review.reviewer}</p> : null}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}

function pos(b: Box) {
  const [x0, y0, x1, y1] = b;
  return { left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` };
}

function tone(f: FieldDecision) {
  if (f.review) return "border-ink bg-ink/5";
  return f.status === "accepted" ? "border-leaf bg-leaf/10" : "border-ink bg-tag/40";
}

function Mark({ box, cls, land }: { box: Box; cls: string; land?: boolean }) {
  return <div className={`pointer-events-none absolute rounded-sm border-2 ${cls} ${land ? "animate-land" : ""}`} style={pos(box)} />;
}

function Pill({ f }: { f: FieldDecision }) {
  if (f.review) return <span className="rounded-full bg-ink px-2.5 py-0.5 text-xs text-paper">{f.review.action === "approve" ? "Approved" : "Corrected"}</span>;
  if (f.status === "accepted") return <span className="rounded-full bg-leaf/20 px-2.5 py-0.5 text-xs text-forest">Accepted</span>;
  return <span className="rounded-full bg-tag px-2.5 py-0.5 text-xs font-bold">Check</span>;
}
