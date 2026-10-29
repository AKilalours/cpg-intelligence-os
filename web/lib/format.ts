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
