import type { Metadata } from "next";
import type { ReactNode } from "react";
// Fonts are bundled (SIL Open Font License), so builds never depend on reaching Google Fonts.
import "@fontsource/fraunces/300.css";
import "@fontsource/fraunces/400.css";
import "@fontsource/lato/400.css";
import "@fontsource/lato/700.css";
import { Nav } from "@/components/Nav";
import "./globals.css";


export const metadata: Metadata = {
  title: "CPG Intelligence OS",
  description: "Decision intelligence for CPG finance and planning: documents, cash application, deductions, forecasting",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-paper font-sans text-ink antialiased">
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 bg-sheet px-3 py-2">
          Skip to content
        </a>
        {process.env.CPGOS_READ_ONLY === "1" ? (
          <div className="bg-ink px-4 py-2 text-center text-xs text-paper">
            Read-only public copy · Walmart M5 sales, FATURA and CORD documents (CC BY 4.0), simulated finance records{" "}
            <a className="underline underline-offset-2" href="https://github.com/AKilalours/cpg-intelligence-os">Source and results</a>
          </div>
        ) : null}
        <Nav />
        <main id="main" className="mx-auto max-w-6xl px-5 pb-16 pt-8 md:px-8">{children}</main>
        <footer className="mx-auto max-w-6xl px-5 pb-8 text-xs text-muted md:px-8">
          Independent portfolio project by Akila Lourdes Miriyala Francis. Public data only. Not affiliated with any company.{" "}
          <a className="underline underline-offset-2" href="https://github.com/AKilalours/cpg-intelligence-os/blob/main/TESTING.md">Test it and send a report</a>.
        </footer>
      </body>
    </html>
  );
}
