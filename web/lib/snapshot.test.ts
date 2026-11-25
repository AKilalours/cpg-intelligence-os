import { test } from "node:test";
import assert from "node:assert/strict";
import { snapKey } from "./snapshot.ts";

// The Python recorder and the web reader must derive the same file name from the same request path,
// or every public page silently shows "not available". These cases are mirrored in tests/test_public_snapshot.py.
test("snapshot keys match the recorder", () => {
  assert.equal(snapKey("/api/forecast/series?item=FOODS_3_120&store=CA_3"), "_api_forecast_series_item_FOODS_3_120_store_CA_3");
  assert.equal(snapKey("/api/promo/evidence?dept="), "_api_promo_evidence_dept_");
  assert.equal(snapKey("/api/lost/cell?store=TX_2&week=2016-02-29"), "_api_lost_cell_store_TX_2_week_2016_02_29");
});
