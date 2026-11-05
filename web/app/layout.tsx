import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Fraunces, Lato } from "next/font/google";
import { Nav } from "@/components/Nav";
import "./globals.css";

const serif = Fraunces({ subsets: ["latin"], weight: ["300", "400"], variable: "--font-serif", display: "swap" });
const body = Lato({ subsets: ["latin"], weight: ["400", "700"], variable: "--font-body", display: "swap" });

export const metadata: Metadata = {
  title: "CPG Intelligence OS",
  description: "Evidence-grounded AI for CPG finance: documents, cash, deductions, forecasts",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${serif.variable} ${body.variable}`}>
      <body className="bg-paper font-sans text-ink antialiased">
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 bg-sheet px-3 py-2">
          Skip to content
        </a>
        <Nav />
        <main id="main" className="mx-auto max-w-6xl px-5 pb-16 pt-8 md:px-8">{children}</main>
        <footer className="mx-auto max-w-6xl px-5 pb-8 text-xs text-muted md:px-8">
          Independent portfolio project by Akila Lourdes Miriyala Francis. Public data only. Not affiliated with any company.
        </footer>
      </body>
    </html>
  );
}
