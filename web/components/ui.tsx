import type { ReactNode } from "react";

// One serif line and at most one short sentence: people act on figures, not paragraphs.
export function PageHeader({ title, purpose }: { title: string; purpose?: string }) {
  return (
    <header className="mb-8 max-w-2xl">
      <h1 className="text-4xl font-light tracking-tight md:text-5xl">{title}</h1>
      {purpose ? <p className="mt-2 text-muted">{purpose}</p> : null}
    </header>
  );
}

// The one bold element: money and counts set like a shelf price tag.
// `due` marks a figure that needs action, using the tag yellow.
export function Figure({
  value, label, due = false,
}: { value: string; label: string; due?: boolean }) {
  return (
    <div className="px-5 py-4 first:pl-0">
      <div
        className={
          "font-figure text-5xl font-light leading-none tabular-nums " +
          (due ? "inline-block rounded-md bg-tag px-2 -mx-2" : "")
        }
      >
        {value}
      </div>
      <div className="mt-2 text-sm text-muted">{label}</div>
    </div>
  );
}

export function Section({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="mb-10">
      <div className="mb-3 flex items-baseline justify-between gap-4">
        <h2 className="text-2xl">{title}</h2>
        {note ? <p className="text-sm text-muted">{note}</p> : null}
      </div>
      {children}
    </section>
  );
}

// Empty and error states give direction: what happened and what to run.
export function Notice({
  kind, title, children,
}: { kind: "empty" | "error" | "planned"; title: string; children?: ReactNode }) {
  const tone =
    kind === "error" ? "border-debit" : kind === "planned" ? "border-muted" : "border-ink";
  return (
    <div className={`rounded-xl border-l-4 ${tone} bg-sheet px-4 py-3`} role={kind === "error" ? "alert" : undefined}>
      <p className="font-semibold">{title}</p>
      {children ? <div className="mt-1 text-sm text-muted">{children}</div> : null}
    </div>
  );
}

export function Cmd({ children }: { children: string }) {
  return <code className="rounded-sm bg-paper px-1.5 py-0.5 text-[0.8125rem] text-ink">{children}</code>;
}

export function Status({ value }: { value: string }) {
  const bad = value === "rejected" || value === "failed" || value === "expired" || value === "denied";
  return <span className={bad ? "font-semibold text-debit" : undefined}>{value.replace(/_/g, " ")}</span>;
}
