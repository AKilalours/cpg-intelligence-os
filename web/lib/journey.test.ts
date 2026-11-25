import { test } from "node:test";
import assert from "node:assert/strict";
import { addVisit, focusFrom, focusQuery } from "./journey.ts";

test("recent visits: newest first, no duplicates, capped", () => {
  let l = [] as ReturnType<typeof addVisit>;
  for (let i = 0; i < 10; i++) l = addVisit(l, { href: `/x${i % 9}`, label: "", kind: "", at: i });
  assert.equal(l.length, 8);
  assert.equal(l[0].href, "/x0");
  assert.equal(new Set(l.map((v) => v.href)).size, 8);
});

test("focus carried between pages, and only well-formed codes are trusted", () => {
  assert.deepEqual(focusFrom(focusQuery("FOODS_3_444", "TX_2")), { item: "FOODS_3_444", store: "TX_2" });
  assert.equal(focusFrom("?item=<script>&store=TX_2"), null);
  assert.equal(focusFrom("?item=FOODS_3_444"), null);
});
