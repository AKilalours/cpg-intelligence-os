import { test } from "node:test";
import assert from "node:assert/strict";
import { failureNotice } from "./result.ts";

// Regression: with the API stopped, Lost sales said "No lost-sales flags yet: run publish",
// sending the person to re-run a job that had already run. Down must read as down.
test("API down is an error, not 'nothing published'", () => {
  const n = failureNotice({ status: 0, error: "Can't reach the API at http://127.0.0.1:8100. Start it with: make api" }, "No flags yet", "Run publish");
  assert.equal(n.kind, "error");
  assert.match(n.body, /make api/);
});

test("only a 404 means nothing is published yet", () => {
  assert.deepEqual(failureNotice({ status: 404, error: "x" }, "No flags yet", "Run publish"),
                   { kind: "empty", title: "No flags yet", body: "Run publish" });
  assert.equal(failureNotice({ status: 500, error: "boom" }, "No flags yet", "Run publish").kind, "error");
});
