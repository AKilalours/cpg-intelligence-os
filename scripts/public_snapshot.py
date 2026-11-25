"""Record the real API's answers so the full app can be published as a static, read-only copy.

GitHub Pages serves files only (free, no card), so the public copy of the app cannot ask a database anything.
This script asks the real API, in-process and read-only, every question the app's pages and buttons ask,
and saves each answer under a key derived from the exact request path. web/lib/snapshot.ts derives the same
key, so the public build reads the same answers the local app gets live.

What is recorded: every page's data; every lost-sales week; the forecast lists and their series; promotion
evidence for every department; every investigation case, deduction and review-queue invoice (with its image,
FATURA and CORD, both CC BY 4.0); a grid of error-budget replays. Nothing is written to the database.

Run with the database up:  make public-snapshot   (writes public-snapshot/, about 30 MB)
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient  # noqa: E402

from cpgos.api import app as api  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "public-snapshot"
LIST_LIMIT = 100                       # forecast list size per sort order (web/public-build twins use the same)
PROMO_LIST = 30
FAULTS = (0.0, 0.05, 0.1, 0.2, 0.3)     # replay grid; the public twin snaps the sliders to these
AUDITS = (0.02, 0.05, 0.1, 0.2, 0.33, 0.5)
SEEDS = (1, 2, 3)


def key(path: str) -> str:
    """Must match snapKey() in web/lib/snapshot.ts."""
    return re.sub(r"[^A-Za-z0-9]+", "_", path)


def q(v) -> str:
    """encodeURIComponent for the values used here (ids, item codes, store codes, dates, departments)."""
    return quote(str(v), safe="-_.!~*'()")


class Recorder:
    def __init__(self):
        self.c = TestClient(api.app)
        self.n = 0
        self.bytes = 0
        self.missing: list[str] = []

    def get(self, path: str, required: bool = True):
        p = OUT / "snap" / f"{key(path)}.json"
        if p.exists():
            return json.loads(p.read_text())
        r = self.c.get(path)
        if r.status_code != 200:
            if required:
                raise SystemExit(f"{path} -> {r.status_code}: {r.text[:200]}")
            self.missing.append(path)
            return None
        body = r.content
        p.write_bytes(body)
        self.n += 1
        self.bytes += len(body)
        return r.json()

    def series(self, item: str, store: str):
        return self.get(f"/api/forecast/series?item={q(item)}&store={q(store)}", required=False)


def image(rec: Recorder, doc_id: int) -> None:
    from PIL import Image
    out = OUT / "snap" / "img" / f"{doc_id}.jpg"
    if out.exists():
        return
    r = rec.c.get(f"/api/documents/{doc_id}/image")
    if r.status_code != 200:
        rec.missing.append(f"image {doc_id}")
        return
    im = Image.open(io.BytesIO(r.content)).convert("RGB")
    im.thumbnail((1400, 1800))
    im.save(out, "JPEG", quality=82, optimize=True)
    rec.bytes += out.stat().st_size


def main() -> int:
    # Set here, not at import: tests import this module, and a process-wide read-only flag would break them.
    os.environ["CPGOS_READ_ONLY"] = "1"
    (OUT / "snap" / "img").mkdir(parents=True, exist_ok=True)
    rec = Recorder()
    g = rec.get

    # Today
    for p in ["/api/overview", "/api/overview/flow", "/api/stp", "/api/evaluation/latest?kind=stp_fresh", "/api/drift",
              "/api/proof", "/api/documents/queue?limit=500"]:
        g(p)
    for f in FAULTS:
        for a in AUDITS:
            for s in SEEDS:
                g(f"/api/stp/replay?fault={f:g}&audit={a:g}&seed={s}", required=False)

    # Documents
    for p in ["/api/datasets", "/api/documents/regression"]:
        g(p)
    queue = g("/api/documents/queue?limit=500&boxes=true")
    for it in queue:
        d = it["source_document_id"]
        g(f"/api/documents/{d}", required=False)
        image(rec, d)

    # Cash and deductions
    g("/api/finance/cash-application?limit=100")
    for d in g("/api/deductions?order=priority&limit=1000"):
        g(f"/api/deductions/{d['deduction_id']}", required=False)

    # Investigate
    for c in g("/api/cases"):
        g(f"/api/cases/{c['case_id']}", required=False)
        g(f"/api/cases/{c['case_id']}/picture", required=False)

    # Planning
    keys = g("/api/sales/series-keys?limit=25")
    if keys:
        g(f"/api/sales/series?sku_code={q(keys[0]['sku_code'])}&store={q(keys[0]['store'])}", required=False)
    g("/api/forecast/summary")
    pairs: set[tuple[str, str]] = set()
    for b in g("/api/forecast/find?limit=12"):
        pairs.add((b["item"], b["store"]))
    for sort in ("dollars", "win", "loss"):
        for b in g(f"/api/forecast/find?sort={sort}&limit={LIST_LIMIT}"):
            pairs.add((b["item"], b["store"]))

    # Promotions
    allp = g("/api/promo/evidence?dept=")
    for d in ["FOODS_3", *allp["depts"]]:
        g(f"/api/promo/evidence?dept={q(d)}")
        for b in g(f"/api/forecast/find?q={q(d)}&limit={PROMO_LIST}"):
            pairs.add((b["item"], b["store"]))
    g("/api/forecast/find?q=FOODS_3&limit=8")

    # Lost sales
    lost = g("/api/lost/summary")
    for st in lost["stores"]:
        for wk in lost["weeks"]:
            rows = g(f"/api/lost/cell?store={q(st)}&week={q(wk)}")
            for r in rows[:3]:
                pairs.add((r["item"], st))
    for t in lost["top"]:
        pairs.add((t["item"], t["store"]))
    for item, store in sorted(pairs):
        rec.series(item, store)
        g(f"/api/lost/item?item={q(item)}&store={q(store)}", required=False)

    # Proof and evaluation
    for p in ["/api/evaluation/latest?kind=phase2a", "/api/ingestion-runs?limit=100", "/api/evaluation/routing-history",
              "/api/evaluation/latest?kind=agent"]:
        g(p, required=False)

    imgs = sorted(int(p.stem) for p in (OUT / "snap" / "img").glob("*.jpg"))
    (OUT / "manifest.json").write_text(json.dumps({
        "series": len(pairs), "documents": imgs, "cases": [c["case_id"] for c in g("/api/cases")],
        "deductions": [d["deduction_id"] for d in g("/api/deductions?order=priority&limit=1000")],
        "replay": {"faults": FAULTS, "audits": AUDITS, "seeds": SEEDS}, "list_limit": LIST_LIMIT, "promo_list": PROMO_LIST,
        "missing": rec.missing[:50], "missing_count": len(rec.missing)}, indent=1))
    total = sum(p.stat().st_size for p in (OUT / "snap").rglob("*") if p.is_file())
    print(f"recorded {rec.n:,} answers and {len(imgs)} images; {total / 1e6:.1f} MB in {OUT}; {len(rec.missing)} not available")
    return 0


if __name__ == "__main__":
    sys.exit(main())
