import { Notice, PageHeader, Section } from "@/components/ui";

export default function InvestigationPage() {
  return (
    <>
      <PageHeader
        title="AI Investigation"
        purpose="Ask why a deduction or payment doesn't add up. The agent gathers records, recomputes amounts, cites evidence, and waits for your approval before anything changes."
      />
      <Section title="Investigations">
        <Notice kind="planned" title="Arrives in Phase 3">
          The agent can read the ledger but the database blocks it from changing any amount. Every step is written to an append-only audit log.
        </Notice>
      </Section>
    </>
  );
}
