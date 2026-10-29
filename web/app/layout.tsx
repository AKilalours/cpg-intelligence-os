import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Nav } from "@/components/Nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "CPG Intelligence OS",
  description: "Evidence-grounded AI for CPG financial operations and commercial planning",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 bg-sheet px-3 py-2">
          Skip to content
        </a>
        <div className="mx-auto flex min-h-screen max-w-[1400px] flex-col md:flex-row">
          <aside className="border-b border-rule px-4 py-6 md:w-64 md:shrink-0 md:border-b-0 md:border-r">
            <div className="mb-6 px-3">
              <p className="font-figure text-2xl leading-tight">CPG Intelligence OS</p>
              <p className="mt-1 text-xs text-muted">Independent portfolio project. Data from public sources.</p>
            </div>
            <Nav />
          </aside>
          <main id="main" className="flex-1 px-5 py-8 md:px-10">{children}</main>
        </div>
      </body>
    </html>
  );
}
