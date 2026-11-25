import { test } from "node:test";
import assert from "node:assert/strict";
import { DEDUCTION_RATE, analystDays, band, impact, posFromSales, salesFromPos } from "./impact.ts";

const close = (a: number, b: number) => assert.ok(Math.abs(a - b) < 1e-6 * Math.max(1, Math.abs(b)), `${a} != ${b}`);

test("the worked example in docs/confido_fit.md: a $50M brand", () => {
  const [lo, hi] = band(50e6, DEDUCTION_RATE[0]); // Inmar 5-15% x HighRadius 5-10%
  close(lo, 125_000); close(hi, 750_000);
  const [lo2, hi2] = band(50e6, DEDUCTION_RATE[1]); // HighRadius 10-20%
  close(lo2, 250_000); close(hi2, 1_000_000);
});

test("impact is plain multiplication and refuses nonsense", () => {
  const r = impact(10e6, 0.1, 0.05);
  close(r.deductions, 1e6); close(r.invalid, 50_000);
  assert.throws(() => impact(-1, 0.1, 0.1));
  assert.throws(() => impact(NaN, 0.1, 0.1));
});

test("slider scale round-trips and hits its ends", () => {
  close(salesFromPos(0), 1e6); close(salesFromPos(1000), 2e9);
  for (const s of [3e6, 50e6, 750e6]) assert.ok(Math.abs(salesFromPos(posFromSales(s)) / s - 1) < 0.01);
});

test("the default lands on a round $50M", () => close(salesFromPos(posFromSales(50e6)), 50e6));

test("analyst days at 20 resolved a day", () => close(analystDays(500), 25));
