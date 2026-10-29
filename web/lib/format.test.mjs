// Run: node lib/format.test.mjs  (no test framework needed for pure functions)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("./format.ts", import.meta.url), "utf8")
  .replace(/export /g, "")
  .replace(/\(intPart: string\): string/, "(intPart)")
  .replace(/\(dec: string, currency = "USD"\): string/, '(dec, currency = "USD")')
  .replace(/\(dec: string\): string/, "(dec)")
  .replace(/\(n: number\): string/, "(n)")
  .replace(/\(iso: string\): string/, "(iso)");
const mod = new Function(`${src}; return { money, quantity, count };`)();

assert.equal(mod.money("1234567.80"), "$1,234,567.80");
assert.equal(mod.money("0"), "$0.00");
assert.equal(mod.money("-514.8"), "-$514.80");
assert.equal(mod.money("0.30"), "$0.30");
assert.equal(mod.quantity("12.000"), "12");
assert.equal(mod.quantity("1500.250"), "1,500.25");
assert.equal(mod.count(30490), "30,490");
console.log("format tests passed");
