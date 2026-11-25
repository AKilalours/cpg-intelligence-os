/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // The demo container runs the self-contained server; local development is unaffected.
  ...(process.env.CPGOS_STANDALONE === "1" ? { output: "standalone" } : {}),
  // The public read-only copy (scripts/build_public.sh): static files served from GitHub Pages.
  ...(process.env.CPGOS_PUBLIC === "1"
    ? { output: "export", basePath: process.env.NEXT_PUBLIC_BASE_PATH || undefined, trailingSlash: true, images: { unoptimized: true } }
    : {}),
};
export default nextConfig;
