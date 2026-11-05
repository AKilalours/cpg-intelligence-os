import { Notice, PageHeader, Section } from "@/components/ui";

export default function InvestigationPage() {
  return (
    <>
      <PageHeader
        title="Investigate"
        purpose="Ask why a claim doesn't add up. The agent cites evidence and waits for your approval."
      />
      <Section title="Investigations">
        <Notice kind="planned" title="Arrives in Phase 3">
          The agent can read the ledger but the database blocks it from changing any amount. Every step is written to an append-only audit log.
        </Notice>
      </Section>
    </>
  );
}
