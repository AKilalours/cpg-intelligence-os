import { api } from "@/lib/api";
import { NextSteps } from "@/components/Journey";
import { PageHeader } from "@/components/ui";
import type { ProofCard } from "@/lib/types";
import { Impact } from "./Impact";

export const dynamic = "force-dynamic";

export default async function ImpactPage() {
  const proof = await api<ProofCard[]>("/api/proof");
  const pick = (k: string) => (proof.ok ? proof.data.find((c) => c.key === k) ?? null : null);
  return (
    <>
      <PageHeader title="Impact" purpose="Your sales, times published industry ranges. Every input is yours to move; every range names its source." />
      <Impact measured={{ cash: pick("cash"), agent: pick("agent"), reasons: pick("reasons") }} />
      <NextSteps steps={[
        { href: "/evaluation", title: "See every measured result", detail: "Held-out data, scored once", roles: ["leadership"] },
        { href: "/", title: "Follow a retailer dollar through the system", roles: ["leadership", "deductions"] },
        { href: "/investigation", title: "Watch the agent investigate a claim", roles: ["deductions"] },
      ]} />
    </>
  );
}
