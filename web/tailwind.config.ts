import type { Config } from "tailwindcss";

// Tokens come from the subject: accounting ledger paper and retail shelf tags.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: "#F2F4F0",
        sheet: "#FBFCFA",
        ink: "#1C2B3A",
        muted: "#5D6B64",
        rule: "#C9D8C6",
        tag: "#F4C430",
        debit: "#A8322A",
      },
      fontFamily: {
        sans: ['"Public Sans"', "system-ui", "sans-serif"],
        figure: ['"Barlow Condensed"', '"Public Sans"', "sans-serif"],
      },
    },
  },
  plugins: [],
};
export default config;
