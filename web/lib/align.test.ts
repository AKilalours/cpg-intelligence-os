// Run: node --test --experimental-strip-types lib/align.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { align } from "./align.ts";

const ops = (a: string, b: string) => align(a, b).map((c) => c.op).join(",");

test("identical references agree everywhere", () => { assert.equal(ops("NP0001234", "NP0001234"), Array(9).fill("same").join(",")); });
test("a dropped digit is one deletion", () => { assert.deepEqual(align("NP000134", "NP0001234").filter((c) => c.op !== "same"), [{ a: null, b: "2", op: "insert" }]); });
test("two adjacent digits swapped are one swap, both shown", () => {
  const diff = align("NP0001324", "NP0001234").filter((c) => c.op !== "same");
  assert.equal(diff.length, 2); assert.ok(diff.every((c) => c.op === "swap"));
});
test("one wrong character is a change", () => { assert.deepEqual(align("NP0001235", "NP0001234").filter((c) => c.op !== "same"), [{ a: "5", b: "4", op: "change" }]); });
test("empty against something is all inserts", () => { assert.equal(ops("", "AB"), "insert,insert"); });
