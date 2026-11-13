// Run: node lib/format.test.mjs  (no test framework needed for pure functions)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("./format.ts", import.meta.url), "utf8")
  .replace(/export /g, "")
  .replace(/\(intPart: string\): string/, "(intPart)")
  .replace(/\(dec: string, currency = "USD"\): string/, '(dec, currency = "USD")')
  .replace(/\(dec: string\): string/, "(dec)")
  .replace(/\(n: number\): string/, "(n)")
  .replace(/\(iso: string\): string/, "(iso)")
  .replace(/\(x: number \| null, digits = 1\): string/, "(x, digits = 1)")
  .replace(/\(r: \{[^)]*\}\): string/, "(r)");
const mod = new Function(`${src}; return { money, quantity, count, pct, rateText };`)();

assert.equal(mod.money("1234567.80"), "$1,234,567.80");
assert.equal(mod.money("0"), "$0.00");
assert.equal(mod.money("-514.8"), "-$514.80");
assert.equal(mod.money("0.30"), "$0.30");
assert.equal(mod.quantity("12.000"), "12");
assert.equal(mod.quantity("1500.250"), "1,500.25");
assert.equal(mod.count(30490), "30,490");
assert.equal(mod.pct(0.9015), "90.2%");
assert.equal(mod.pct(null), "n/a");
assert.equal(mod.rateText({ k: 0, n: 875, ci95: [0, 0.0044] }), "0 of 875 (95% CI 0.0% to 0.4%)");
assert.equal(mod.rateText({ k: 0, n: 0, ci95: null }), "none to score");
assert.equal(mod.pct(0.0044), "0.4%");
console.log("format tests passed");
