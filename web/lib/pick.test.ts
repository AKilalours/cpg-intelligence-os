import { test } from "node:test";
import assert from "node:assert/strict";
import { nearestPoint } from "./pick.ts";

// Regression: with one hover target per dot, the top dot of a stack swallowed the pointer and the dots beneath
// it could never be reached or read. Now one nearest point is chosen and the rest are counted.
test("picks the nearest point and counts the ones stacked on it", () => {
  const pts: [number, number, number][] = [[0, 100, 100], [1, 100, 100], [2, 101, 100], [3, 140, 100]];
  const r = nearestPoint(pts, 102, 101)!;
  assert.equal(r.i, 2);
  assert.equal(r.stacked, 2);
});

test("nothing is picked when the pointer is far from every point", () => {
  assert.equal(nearestPoint([[0, 10, 10]], 200, 200), null);
});

test("a point just below a stack is still reachable", () => {
  const pts: [number, number, number][] = [[0, 50, 50], [1, 50, 50], [2, 50, 62]];
  assert.equal(nearestPoint(pts, 50, 61)!.i, 2);
});
