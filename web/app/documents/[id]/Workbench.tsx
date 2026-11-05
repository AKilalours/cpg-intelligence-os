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
  const [natural, setNatural] = useState<[number, number] | null>(null);
  const img = useRef<HTMLImageElement | null>(null);
  // A cached image can finish loading before React attaches onLoad, so also read it once mounted.
  useEffect(() => {
    const el = img.current;
    if (el?.complete && el.naturalWidth) setNatural([el.naturalWidth, el.naturalHeight]);
  }, []);
  // Where each field is on the page: the decision's box, else the first reader that located it.
  const boxOf = useCallback((f: FieldDecision): Box | null =>
    f.bbox ?? doc.readings.find((r) => r.field === f.field && r.bbox)?.bbox ?? null, [doc.readings]);
  const imgSrc = `/documents/${doc.source_document_id}/image`;
  const openField = fields.find((f) => f.field === open) ?? null;
  const openBox = openField ? boxOf(openField) : null;
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
          <img ref={img} src={imgSrc} alt={`Invoice ${doc.source_document_id}`} className="block w-full"
               onLoad={(e) => setNatural([e.currentTarget.naturalWidth, e.currentTarget.naturalHeight])} />
          {decided && openBox ? (
            <div className="pointer-events-none absolute rounded-sm transition-all duration-300"
                 style={{ ...pos(pad(openBox, 0.006)), boxShadow: "0 0 0 9999px rgba(31,42,36,0.38)" }} />
          ) : null}
          {decided && openField ? doc.readings.filter((r) => r.field === openField.field && r.bbox).map((r) => (
            <div key={`o-${r.reader}`} className={`pointer-events-none absolute rounded-sm border-2 border-dashed animate-land ${READER_TONE[r.reader]}`}
                 style={pos(r.bbox!)} title={READER[r.reader]} />
          )) : null}
          {playing ? <div className="pointer-events-none absolute inset-x-0 h-0.5 animate-sweep bg-sky/60" /> : null}
          {shown.map((s, i) => s.kind === "read" && s.r.bbox ? (
            <Mark key={`r${i}`} box={s.r.bbox} cls={decided ? "border-transparent" :
              s.r.reader === "rules" ? "border-ink/60" : "border-sky"} land />
          ) : null)}
          {decided ? fields.map((f) => boxOf(f) ? (
            <button key={f.field} onClick={() => setOpen(f.field)} onMouseEnter={() => setHover(f.field)} onMouseLeave={() => setHover(null)}
                    aria-label={`${label(f.field)}: ${f.value ?? "no value"}`}
                    className={"absolute rounded-sm border-2 transition " + tone(f) + (hover === f.field || open === f.field ? " ring-4 ring-tag" : "")}
                    style={pos(boxOf(f)!)} />
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
                    {f.review || f.value !== null ? (
                      <span className="font-serif text-xl tabular-nums">{f.review ? (f.review.value ?? "not on page") : f.value}</span>
                    ) : <Split candidates={f.candidates} />}
                  </span>
                  <Pill f={f} />
                </button>
                {isOpen ? (
                  <div className="border-t border-rule px-4 pb-4 pt-3 text-sm">
                    {boxOf(f) && natural ? <Loupe src={imgSrc} box={pad(boxOf(f)!, 0.02)} natural={natural} /> : null}
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

const READER_TONE: Record<string, string> = { rules: "border-paper", local_model: "border-sky", claude: "border-tag" };

function pad(b: Box, m: number): Box {
  return [Math.max(0, b[0] - m), Math.max(0, b[1] - m), Math.min(1, b[2] + m), Math.min(1, b[3] + m)];
}

// What each reader saw, side by side: the disagreement is the reason a person is asked.
function Split({ candidates }: { candidates: Record<string, string> }) {
  const e = Object.entries(candidates);
  if (e.length === 0) return <span className="font-serif text-xl text-muted">Not found</span>;
  const same = new Set(e.map(([, v]) => v)).size === 1;
  return (
    <span className="mt-0.5 flex flex-wrap items-baseline gap-x-3 gap-y-1">
      {e.map(([k, v]) => (
        <span key={k} className="tabular-nums">
          <span className="font-serif text-xl">{v}</span>
          <span className="ml-1 text-xs text-muted">{READER[k] ?? k}</span>
        </span>
      ))}
      {!same ? <span className="rounded bg-debit/10 px-1.5 text-xs font-semibold text-debit">disagree</span> : null}
    </span>
  );
}

// A magnified crop of the field on the page, so the digits can be checked without squinting.
// The crop is first grown to a readable shape (between 2.2:1 and 6:1) around the field, so the
// image is scaled the same on both axes and never stretched.
const MIN_CROP = 0.34;

function Loupe({ src, box, natural }: { src: string; box: Box; natural: [number, number] }) {
  const [nw, nh] = natural;
  let [x0, y0, x1, y1] = box;
  if (x1 - x0 < MIN_CROP) { const c = (x0 + x1) / 2; x0 = c - MIN_CROP / 2; x1 = c + MIN_CROP / 2; }  // at most ~3x zoom: sharp, with context
  const ratio = ((x1 - x0) * nw) / ((y1 - y0) * nh);
  const R = Math.min(Math.max(ratio, 2.2), 6);
  if (ratio < R) {                                   // too tall: widen around the centre
    const w = ((y1 - y0) * nh * R) / nw, c = (x0 + x1) / 2;
    x0 = c - w / 2; x1 = c + w / 2;
  } else if (ratio > R) {                            // too wide: add height around the centre
    const h = ((x1 - x0) * nw) / (R * nh), c = (y0 + y1) / 2;
    y0 = c - h / 2; y1 = c + h / 2;
  }
  const shift = (a: number, b: number): [number, number] => (b - a >= 1 ? [0, 1] : a < 0 ? [0, b - a] : b > 1 ? [1 - (b - a), 1] : [a, b]);
  [x0, x1] = shift(x0, x1); [y0, y1] = shift(y0, y1);
  const bw = x1 - x0, bh = y1 - y0;
  return (
    <div className="mb-3 overflow-hidden rounded-lg border border-rule bg-paper animate-rise" aria-label="Magnified view of the field">
      <div className="w-full" style={{
        aspectRatio: String((bw * nw) / (bh * nh)),
        backgroundImage: `url(${src})`, backgroundRepeat: "no-repeat",
        backgroundSize: `${100 / bw}% ${100 / bh}%`,
        backgroundPosition: `${bw < 1 ? (x0 / (1 - bw)) * 100 : 0}% ${bh < 1 ? (y0 / (1 - bh)) * 100 : 0}%`,
      }} />
    </div>
  );
}
