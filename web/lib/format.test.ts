// Run: npm test (node --test --experimental-strip-types). Imports the real format.ts: the old format.test.mjs
// stripped TypeScript types with search-and-replace and broke silently when toCents/fromCents were added.
import { test } from "node:test";
import assert from "node:assert/strict";
import { count, fromCents, money, pct, quantity, rateText, toCents } from "./format.ts";

test("money", () => {
  assert.equal(money("1234567.80"), "$1,234,567.80");
  assert.equal(money("0"), "$0.00");
  assert.equal(money("-514.8"), "-$514.80");
  assert.equal(money("0.30"), "$0.30");
});

test("quantities, counts and percentages", () => {
  assert.equal(quantity("12.000"), "12");
  assert.equal(quantity("1500.250"), "1,500.25");
  assert.equal(count(30490), "30,490");
  assert.equal(pct(0.9015), "90.2%");
  assert.equal(pct(null), "n/a");
  assert.equal(pct(0.0044), "0.4%");
});

test("rates carry their count and interval", () => {
  assert.equal(rateText({ k: 0, n: 875, ci95: [0, 0.0044] }), "0 of 875 (95% CI 0.0% to 0.4%)");
  assert.equal(rateText({ k: 0, n: 0, ci95: null }), "none to score");
});

test("cents are exact: no floating point between the ledger and the screen", () => {
  assert.equal(toCents("1557.36"), 155736n);
  assert.equal(toCents("-514.8"), -51480n);
  assert.equal(toCents(null), 0n);
  assert.equal(fromCents(toCents("3492.00") - toCents("1557.36")), "1934.64");
  assert.equal(toCents("0.1") + toCents("0.2"), toCents("0.3"));          // 0.1 + 0.2 !== 0.3 in floats
});
