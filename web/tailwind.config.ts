import type { Config } from "tailwindcss";

// Tokens come from the subject: accounting ledger paper and retail shelf tags.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: "#FAF8F4",       // the page: warm cream
        sheet: "#FFFFFF",       // cards and tables
        ink: "#1F2A24",         // text: green-black
        muted: "#6C6C6C",
        rule: "#E4E1D8",
        forest: "#28543E",      // primary actions
        leaf: "#609D7B",        // accepted / done
        tag: "#E5FF87",         // needs you now (lime): used sparingly
        sky: "#2881D4",         // the AI readers
        debit: "#B3402F",       // errors, money at risk
        credit: "#2E6B4A",
      },
      fontFamily: {
        sans: ["var(--font-body)", "system-ui", "sans-serif"],
        figure: ["var(--font-serif)", "Georgia", "serif"],
        serif: ["var(--font-serif)", "Georgia", "serif"],
      },
      keyframes: {
        land: { "0%": { opacity: "0", transform: "scale(1.25)" }, "100%": { opacity: "1", transform: "scale(1)" } },
        sweep: { "0%": { top: "0%" }, "100%": { top: "100%" } },
        grow: { "0%": { transform: "scaleX(0)" }, "100%": { transform: "scaleX(1)" } },
        rise: { "0%": { opacity: "0", transform: "translateY(10px)" }, "100%": { opacity: "1", transform: "none" } },
        draw: { "0%": { strokeDashoffset: "var(--len)" }, "100%": { strokeDashoffset: "var(--off)" } },
        flow: { "0%": { strokeDashoffset: "24" }, "100%": { strokeDashoffset: "0" } },
      },
      animation: {
        land: "land 260ms ease-out both", sweep: "sweep 1.4s linear infinite",
        grow: "grow 900ms cubic-bezier(.2,.7,.2,1) both", rise: "rise 320ms ease-out both",
        draw: "draw 900ms cubic-bezier(.2,.7,.2,1) both", flow: "flow 900ms linear infinite",
      },
    },
  },
  plugins: [],
};
export default config;
