import Link from "next/link";
import { api } from "@/lib/api";
import type { DocumentView, ReviewQueueItem } from "@/lib/types";
import { Notice, PageHeader } from "@/components/ui";
import { Workbench } from "./Workbench";

export const dynamic = "force-dynamic";

export default async function DocumentReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [res, queue] = await Promise.all([
    api<DocumentView>(`/api/documents/${id}`),
    api<ReviewQueueItem[]>("/api/documents/queue?limit=500"),
  ]);
  if (!res.ok) {
    return (
      <>
        <PageHeader title={`Invoice #${id}`} />
        <Notice kind="error" title="Invoice unavailable">{res.error}</Notice>
      </>
    );
  }
  const others = queue.ok ? queue.data.filter((q) => q.source_document_id !== res.data.source_document_id) : [];
  const nextId = others[0]?.source_document_id ?? null;
  return (
    <>
      <div className="mb-6 flex flex-wrap items-baseline justify-between gap-3">
        <h1 className="font-serif text-4xl font-light md:text-5xl">Invoice #{res.data.source_document_id}</h1>
        <div className="flex gap-3 text-sm">
          <Link href="/documents" className="rounded-lg border border-rule bg-sheet px-3 py-1.5 hover:border-forest">Queue</Link>
          {nextId ? <Link href={`/documents/${nextId}`} className="rounded-lg border border-rule bg-sheet px-3 py-1.5 hover:border-forest">Skip</Link> : null}
        </div>
      </div>
      <Workbench doc={res.data} nextId={nextId} />
    </>
  );
}
