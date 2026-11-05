"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import type { ReviewQueueItem } from "@/lib/types";

const PAGE = 12;
const label = (f: string) => f.replace(/_/g, " ");

export function QueueBrowser({ items }: { items: ReviewQueueItem[] }) {
  const [field, setField] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const counts = useMemo(() => {
    const c = new Map<string, number>();
    for (const q of items) for (const f of q.review_fields) c.set(f, (c.get(f) ?? 0) + 1);
    return [...c.entries()].sort((a, b) => b[1] - a[1]);
  }, [items]);
  const shown = field ? items.filter((q) => q.review_fields.includes(field)) : items;
  const pages = Math.max(1, Math.ceil(shown.length / PAGE));
  const rows = shown.slice(page * PAGE, page * PAGE + PAGE);
  const open = items.reduce((n, q) => n + q.open_fields, 0);

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-end gap-x-10 gap-y-4">
        <div>
          <div className="font-serif text-6xl font-light tabular-nums">{items.length}</div>
          <div className="text-sm text-muted">invoices need you</div>
        </div>
        <div>
          <div className="font-serif text-6xl font-light tabular-nums">{open}</div>
          <div className="text-sm text-muted">fields to check</div>
        </div>
        {shown.length ? (
          <Link href={`/documents/${shown[0].source_document_id}`}
                className="ml-auto rounded-lg bg-ink px-5 py-3 font-bold text-paper hover:bg-forest">
            Start reviewing
          </Link>
        ) : null}
      </div>

      <div className="mb-4 flex flex-wrap gap-2" role="group" aria-label="Filter by field">
        <Chip active={field === null} onClick={() => { setField(null); setPage(0); }}>All {items.length}</Chip>
        {counts.map(([f, n]) => (
          <Chip key={f} active={field === f} onClick={() => { setField(field === f ? null : f); setPage(0); }}>
            {label(f)} {n}
          </Chip>
        ))}
      </div>

      <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {rows.map((q) => (
          <li key={q.source_document_id}>
            <Link href={`/documents/${q.source_document_id}`}
                  className="group flex h-full flex-col rounded-xl border border-rule bg-sheet p-4 transition hover:-translate-y-0.5 hover:border-forest hover:shadow-[0_6px_20px_rgba(31,42,36,0.08)]">
              <div className="flex items-baseline justify-between">
                <span className="font-serif text-2xl font-light">#{q.source_document_id}</span>
                <span className="rounded-md bg-tag px-2 py-0.5 text-sm font-bold">{q.open_fields}</span>
              </div>
              <div className="mt-3 flex flex-wrap gap-1">
                {q.review_fields.map((f) => (
                  <span key={f} className={"rounded-md px-2 py-0.5 text-xs " + (f === field ? "bg-forest text-paper" : "bg-paper")}>{label(f)}</span>
                ))}
              </div>
              <span className="mt-3 text-xs text-muted group-hover:text-forest">{q.accepted_fields} already accepted</span>
            </Link>
          </li>
        ))}
      </ul>

      {pages > 1 ? (
        <div className="mt-4 flex items-center gap-3 text-sm">
          <button onClick={() => setPage(Math.max(0, page - 1))} disabled={page === 0}
                  className="rounded-lg border border-rule bg-sheet px-3 py-1 disabled:opacity-40">Previous</button>
          <span className="text-muted tabular-nums">{page + 1} / {pages}</span>
          <button onClick={() => setPage(Math.min(pages - 1, page + 1))} disabled={page >= pages - 1}
                  className="rounded-lg border border-rule bg-sheet px-3 py-1 disabled:opacity-40">Next</button>
        </div>
      ) : null}
    </div>
  );
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} aria-pressed={active}
            className={"rounded-full border px-3 py-1 text-sm transition " +
              (active ? "border-forest bg-forest text-paper" : "border-rule bg-sheet hover:border-forest")}>
      {children}
    </button>
  );
}
