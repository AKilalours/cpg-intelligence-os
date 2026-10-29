"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const SECTIONS = [
  { href: "/", label: "Overview", hint: "Exposure, exceptions, data health" },
  { href: "/documents", label: "Documents", hint: "Upload, extract, validate" },
  { href: "/finance", label: "Finance", hint: "Cash, deductions, disputes" },
  { href: "/planning", label: "Planning", hint: "Forecasts, promotions, anomalies" },
  { href: "/investigation", label: "AI Investigation", hint: "Evidence and recommendations" },
  { href: "/evaluation", label: "Evaluation Lab", hint: "Benchmarks, failures, regressions" },
];

export function Nav() {
  const path = usePathname();
  return (
    <nav aria-label="Sections" className="flex flex-col gap-1">
      {SECTIONS.map((s) => {
        const active = s.href === "/" ? path === "/" : path.startsWith(s.href);
        return (
          <Link
            key={s.href}
            href={s.href}
            aria-current={active ? "page" : undefined}
            className={
              "rounded-sm px-3 py-2 border-l-4 " +
              (active ? "border-ink bg-sheet" : "border-transparent hover:bg-sheet/60")
            }
          >
            <span className="block font-semibold">{s.label}</span>
            <span className="block text-xs text-muted">{s.hint}</span>
          </Link>
        );
      })}
    </nav>
  );
}
