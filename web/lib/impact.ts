// The impact model: a brand's own numbers times published industry ranges. Pure arithmetic, no
// Confido or customer data. Every range names its source; the page links them.

export interface Source { name: string; url: string; quote: string }
export interface Range { lo: number; hi: number; source: Source }

export const HIGHRADIUS: Source = {
  name: "HighRadius (citing the Credit Research Foundation)",
  url: "https://www.highradius.com/insights/finsider/deduction-metrics-enterprises/",
  quote: "deductions can take 10-20% of revenue; about 5-10% of deduction value is invalid; about 20 resolved per analyst per day",
};
export const INMAR: Source = {
  name: "Inmar",
  url: "https://www.inmar.com/blog/insights/martech/hidden-cost-cpg-chargebacks-what-manufacturers-overlook",
  quote: "deductions account for 5-15% of gross sales in CPG",
};

// Share of gross sales taken as deductions, per source.
export const DEDUCTION_RATE: Range[] = [
  { lo: 0.05, hi: 0.15, source: INMAR },
  { lo: 0.10, hi: 0.20, source: HIGHRADIUS },
];
// Share of deduction value that is invalid (only HighRadius states this as a share of value).
export const INVALID_SHARE: Range = { lo: 0.05, hi: 0.10, source: HIGHRADIUS };
export const RESOLVED_PER_ANALYST_DAY = 20; // HighRadius

export interface Impact { deductions: number; invalid: number }

export function impact(sales: number, deductionRate: number, invalidShare: number): Impact {
  if (!(sales >= 0) || !(deductionRate >= 0) || !(invalidShare >= 0)) throw new Error("inputs must be non-negative numbers");
  const deductions = sales * deductionRate;
  return { deductions, invalid: deductions * invalidShare };
}

// The band each source implies for invalid dollars at stake: low rate x low share to high x high.
export function band(sales: number, d: Range, v: Range = INVALID_SHARE): [number, number] {
  return [impact(sales, d.lo, v.lo).invalid, impact(sales, d.hi, v.hi).invalid];
}

export function analystDays(deductionsPerMonth: number): number {
  return deductionsPerMonth / RESOLVED_PER_ANALYST_DAY;
}

// Log-scale slider: position 0..1000 <-> $1M..$2B, so small and large brands both get room.
const LO = Math.log10(1e6), HI = Math.log10(2e9);
// Snapped to two significant figures, so the slider lands on $50M rather than $50.1M.
const snap = (x: number) => { const k = 10 ** (Math.floor(Math.log10(x)) - 1); return Math.round(x / k) * k; };
export const salesFromPos = (p: number) => snap(10 ** (LO + (HI - LO) * (p / 1000)));
export const posFromSales = (s: number) => Math.round(((Math.log10(s) - LO) / (HI - LO)) * 1000);
