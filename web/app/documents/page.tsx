import { api } from "@/lib/api";
import { count } from "@/lib/format";
import type { DatasetStatus } from "@/lib/types";
import { Cmd, Notice, PageHeader, Section } from "@/components/ui";

export const dynamic = "force-dynamic";

export default async function DocumentsPage() {
  const res = await api<DatasetStatus[]>("/api/datasets");
  return (
    <>
      <PageHeader
        title="Documents"
        purpose="Turn invoices, remittances, deduction notices, and promotion agreements into validated, typed records with a source for every field."
      />
      <Section title="Document extraction">
        <Notice kind="planned" title="Arrives in Phase 2">
          Extraction runs on DocILE business documents and FATURA invoices, scored per field and per layout.
        </Notice>
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
