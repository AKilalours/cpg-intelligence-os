import { api } from "@/lib/api";
import { count } from "@/lib/format";
import type { DatasetStatus, ReviewQueueItem } from "@/lib/types";
import Link from "next/link";
import { Cmd, Notice, PageHeader, Section } from "@/components/ui";

export const dynamic = "force-dynamic";

export default async function DocumentsPage() {
  const [res, queue] = await Promise.all([
    api<DatasetStatus[]>("/api/datasets"),
    api<ReviewQueueItem[]>("/api/documents/queue"),
  ]);
  return (
    <>
      <PageHeader
        title="Documents"
        purpose="Invoices read by three readers; a routing rule accepts what is safe, and people check the rest. Every review is attributed and kept."
      />
      <Section title="Review queue" note="Documents with fields the routing rule would not accept on its own">
        {!queue.ok ? (
          <Notice kind="error" title="Review queue unavailable">{queue.error}</Notice>
        ) : queue.data.length === 0 ? (
          <Notice kind="empty" title="Nothing waiting for a person">
            Store routing decisions first: <Cmd>make decide-docs ROUND=5</Cmd>
          </Notice>
        ) : (
          <div className="overflow-x-auto">
            <table className="ledger bg-sheet">
              <thead>
                <tr><th>Document</th><th className="num">Waiting</th><th className="num">Accepted</th><th>Fields to check</th><th>Rule</th></tr>
              </thead>
              <tbody>
                {queue.data.map((q) => (
                  <tr key={q.source_document_id}>
                    <td><Link className="underline underline-offset-4" href={`/documents/${q.source_document_id}`}>{q.source_document_id}</Link></td>
                    <td className="num">{count(q.open_fields)}</td>
                    <td className="num">{count(q.accepted_fields)}</td>
                    <td>{q.review_fields.map((f) => f.replace(/_/g, " ")).join(", ")}</td>
                    <td className="text-sm text-muted">{q.policy_version}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
      <Section title="Source data readiness" note="Every dataset needs accepted terms and downloaded files before it loads">
        {!res.ok ? (
          <Notice kind="error" title="Dataset status unavailable">{res.error}</Notice>
        ) : (
          <div className="overflow-x-auto">
            <table className="ledger bg-sheet">
              <thead>
                <tr>
                  <th>Dataset</th><th>Origin</th><th>How to get it</th><th>Terms</th>
                  <th>Files</th><th className="num">Versions loaded</th>
                </tr>
              </thead>
              <tbody>
                {res.data.map((d) => (
                  <tr key={d.key}>
                    <td>
                      <a className="underline decoration-rule underline-offset-4 hover:decoration-ink" href={d.landing_page}>
                        {d.title}
                      </a>
                    </td>
                    <td>{d.origin.replace(/_/g, " ")}</td>
                    <td>{d.method === "manual" ? "Request or register, then place files" : d.method === "bundled" ? "Included in repo" : <Cmd>{`download_data.py ${d.key}`}</Cmd>}</td>
                    <td>
                      {d.license_accepted ? "Accepted" : "Not accepted"}
                      {!d.license_verified ? <div className="text-xs text-muted">Read before use</div> : null}
                    </td>
                    <td>{d.files_present ? "Present" : "Missing"}</td>
                    <td className="num">{count(d.versions_loaded)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </>
  );
}
