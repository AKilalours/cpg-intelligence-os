"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const SECTIONS = [
  { href: "/", label: "Today" },
  { href: "/documents", label: "Documents" },
  { href: "/finance", label: "Cash & deductions" },
  { href: "/planning", label: "Planning" },
  { href: "/promotions", label: "Promotions" },
  { href: "/investigation", label: "Investigate" },
  { href: "/evaluation", label: "Proof" },
];

export function Nav() {
  const path = usePathname();
  return (
    <header className="sticky top-0 z-20 px-3 pt-3 md:px-6">
      <nav aria-label="Sections"
           className="mx-auto flex max-w-6xl items-center gap-1 overflow-x-auto rounded-2xl border border-rule bg-sheet/90 px-3 py-2 shadow-[0_2px_12px_rgba(31,42,36,0.06)] backdrop-blur">
        <Link href="/" className="mr-3 shrink-0 font-serif text-xl font-light tracking-tight">cpg<span className="text-forest">·</span>os</Link>
        {SECTIONS.map((s) => {
          const active = s.href === "/" ? path === "/" : path.startsWith(s.href);
          return (
            <Link key={s.href} href={s.href} aria-current={active ? "page" : undefined}
                  className={"shrink-0 rounded-lg px-3 py-1.5 text-sm transition-colors " +
                    (active ? "bg-forest text-paper" : "text-ink hover:bg-paper")}>
              {s.label}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}
