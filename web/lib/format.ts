// Formatting without floats: decimal strings are regrouped as text, so
// "1234567.80" becomes "$1,234,567.80" with no rounding anywhere.

function groupThousands(intPart: string): string {
  return intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

export function money(dec: string, currency = "USD"): string {
  const neg = dec.startsWith("-");
  const [i, f = ""] = (neg ? dec.slice(1) : dec).split(".");
  const cents = (f + "00").slice(0, 2);
  const symbol = currency === "USD" ? "$" : `${currency} `;
  return `${neg ? "-" : ""}${symbol}${groupThousands(i || "0")}.${cents}`;
}

export function quantity(dec: string): string {
  const [i, f] = dec.split(".");
  const trimmed = f ? f.replace(/0+$/, "") : "";
  return groupThousands(i) + (trimmed ? `.${trimmed}` : "");
}

export function count(n: number): string {
  return groupThousands(String(n));
}

export function when(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

// A rate with its evidence: "0 of 875 (95% CI 0.0 to 0.4%)".
export function pct(x: number | null, digits = 1): string {
  if (x === null) return "n/a";
  // 0.9015 * 100 is 90.14999... in binary floating point; snap to 12 significant
  // digits first so the displayed rounding matches the decimal the API sent.
  const scaled = Number((x * 100).toPrecision(12));
  const f = 10 ** digits;
  return `${(Math.round(scaled * f) / f).toFixed(digits)}%`;
}

export function rateText(r: { k: number; n: number; ci95: [number, number] | null }): string {
  if (r.n === 0) return "none to score";
  const ci = r.ci95 ? ` (95% CI ${pct(r.ci95[0])} to ${pct(r.ci95[1])})` : "";
  return `${count(r.k)} of ${count(r.n)}${ci}`;
}
