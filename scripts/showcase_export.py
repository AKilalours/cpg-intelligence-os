"""Export what the free public showcase (GitHub Pages) shows, as small JSON files in site/json/.

GitHub Pages hosts static files only, for free and with no card, so the showcase cannot query a database.
This asks the real API (in-process, read-only) for the same answers the app shows and saves them.
Aggregates only: weekly totals, scores and flags. No raw dataset files, no document images, nothing from DocILE.

Run with the database up:  make showcase-data
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient  # noqa: E402

from cpgos.api import app as api  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "site" / "json"
TOP_SERIES = 40          # forecast series the showcase lets you open
TOP_ITEMS = 40           # lost-sales items with a full weekly chart


def get(c: TestClient, path: str):
    r = c.get(path)
    if r.status_code != 200:
        raise SystemExit(f"{path} -> {r.status_code}: {r.text[:200]}")
    return r.json()


def slim_series(s: dict) -> dict:
    keep = ("item", "store", "dept", "class", "dollars", "lgbm_rmsse", "ses_rmsse", "weeks", "future_weeks",
            "history", "actual", "test_lo", "test_hi", "future", "future_lo", "future_hi")
    out = {k: s[k] for k in keep if k in s}
    out["test"] = {k: v for k, v in s.get("test", {}).items() if k in ("lgbm", "ses")}
    # The last two years are enough to read a chart and keep the file small.
    n = len(out["weeks"]) - 104
    if n > 0:
        out["weeks"] = out["weeks"][n:]
        out["history"] = out["history"][n:] if len(out["history"]) > n else out["history"]
    return out


def main() -> int:
    # Set here, not at import: tests import this module, and a process-wide read-only flag would break them.
    os.environ["CPGOS_READ_ONLY"] = "1"
    OUT.mkdir(parents=True, exist_ok=True)
    c = TestClient(api.app)
    files: dict[str, object] = {}
    files["proof"] = get(c, "/api/proof")
    files["forecast_summary"] = get(c, "/api/forecast/summary")
    top = get(c, f"/api/forecast/find?sort=dollars&limit={TOP_SERIES}")
    files["forecast_series"] = [slim_series(get(c, f"/api/forecast/series?item={b['item']}&store={b['store']}")) for b in top]
    allp = get(c, "/api/promo/evidence")
    files["promo"] = {"all": allp, **{d: get(c, f"/api/promo/evidence?dept={d}") for d in allp["depts"]}}
    lost = get(c, "/api/lost/summary")
    files["lost_summary"] = lost
    cells = {}
    for st in lost["stores"]:
        for wk in lost["weeks"]:
            rows = get(c, f"/api/lost/cell?store={st}&week={wk}")
            cells[f"{st}|{wk}"] = {"n": len(rows), "rows": rows[:12]}
    files["lost_cells"] = cells
    items = {}
    for t in lost["top"][:TOP_ITEMS]:
        q = f"item={t['item']}&store={t['store']}"
        s = get(c, f"/api/forecast/series?{q}")
        items[f"{t['item']}|{t['store']}"] = {"weeks": s["weeks"], "sold": s["history"] + s["actual"],
                                              "flags": get(c, f"/api/lost/item?{q}")}
    files["lost_items"] = items
    import datetime
    import subprocess
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    video = (OUT.parent / "video.txt").read_text().strip() if (OUT.parent / "video.txt").exists() else None
    files["meta"] = {"exported": datetime.date.today().isoformat(), "commit": commit, "video": video}
    total = 0
    for name, data in files.items():
        p = OUT / f"{name}.json"
        p.write_text(json.dumps(data, separators=(",", ":")))
        total += p.stat().st_size
        print(f"{p.name:24s} {p.stat().st_size / 1e3:8,.0f} KB")
    print(f"total {total / 1e6:.1f} MB in {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
