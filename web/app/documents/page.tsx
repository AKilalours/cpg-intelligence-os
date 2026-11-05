import { api } from "@/lib/api";
import { count } from "@/lib/format";
import type { DatasetStatus, RegressionGate, ReviewQueueItem } from "@/lib/types";
import { Gate } from "./Gate";
import { QueueBrowser } from "./QueueBrowser";
import { Cmd, Notice, PageHeader } from "@/components/ui";

export const dynamic = "force-dynamic";

export default async function DocumentsPage() {
  const [res, queue, gate] = await Promise.all([
    api<DatasetStatus[]>("/api/datasets"),
    api<ReviewQueueItem[]>("/api/documents/queue?limit=500"),
    api<RegressionGate>("/api/documents/regression"),
  ]);
  return (
    <>
      <PageHeader
        title="Documents"
        purpose="Three readers per invoice. You check only what they can't settle."
      />
      {gate.ok ? <Gate g={gate.data} /> : null}
      {!queue.ok ? (
        <Notice kind="error" title="Review queue unavailable">{queue.error}</Notice>
      ) : queue.data.length === 0 ? (
        <Notice kind="empty" title="Nothing waiting for you">
          Load decisions: <Cmd>make decide-docs ROUND=5</Cmd>
        </Notice>
      ) : (
        <QueueBrowser items={queue.data} />
      )}

      <details className="mt-12 rounded-xl border border-rule bg-sheet p-4">
        <summary className="cursor-pointer font-bold">Data sources</summary>
        <div className="mt-4">
      <div>
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
      </div>
        </div>
      </details>
    </>
  );
}
