/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // The demo container runs the self-contained server; local development is unaffected.
  ...(process.env.CPGOS_STANDALONE === "1" ? { output: "standalone" } : {}),
};
export default nextConfig;
