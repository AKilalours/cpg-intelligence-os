"""Phase 2b commands for the Document Intelligence Engine.

Usage:
    python scripts/documents.py load-fatura    # register FATURA invoices and write the answer key to eval.*
    python scripts/documents.py train-router   # learn layout fingerprints (fit layouts); choose grid and threshold on calibration
    python scripts/documents.py eval-router    # score the frozen router once on held-out invoices; store the report
    python scripts/documents.py route          # route every document as the engine role

Requires CPGOS_DATABASE_URL and FATURA in data/raw/fatura (make download DATASET=fatura).
No language model is called by these commands.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cpgos.preflight import require_packages  # noqa: E402  (stdlib only)

require_packages()

from cpgos.db import code_version, connect, migrate  # noqa: E402
from cpgos.documents import layout  # noqa: E402
from cpgos.documents.fatura import FaturaFormatError, load_fatura  # noqa: E402

RAW = ROOT / "data" / "raw"
LOCK = ROOT / "data" / "datasets.lock.json"


def cmd_load_fatura(conn) -> int:
    zip_sha = None
    if LOCK.exists():
        zip_sha = json.loads(LOCK.read_text()).get("fatura", {}).get("FATURA2.zip")
    t = time.time()
    try:
        rep = load_fatura(conn, RAW, zip_sha256=zip_sha)
    except FaturaFormatError as e:
        print(f"BLOCKED: {e}")
        return 1
    out = asdict(rep)
    out["provenance"] = "FATURA2.zip sha256 from datasets.lock.json" if zip_sha else "manifest hash of files"
    out["seconds"] = round(time.time() - t, 1)
    print(json.dumps(out, indent=1))
    return 0


def cmd_train_router(conn) -> int:
    t = time.time()
    router, info = layout.train(conn, RAW)
    mid = layout.save(conn, router)
    print(json.dumps({"layout_model_id": mid, "grid": list(router.grid), "threshold": round(router.threshold, 4),
                      "layouts": len(router.keys), "selection_on_calibration": info["selection"],
                      "seconds": round(time.time() - t, 1)}, indent=1, default=str))
    return 0


def cmd_eval_router(conn) -> int:
    from psycopg.types.json import Jsonb
    t = time.time()
    mid, router = layout.load(conn)
    rep = layout.evaluate(conn, RAW, router)
    rep["layout_model_id"] = mid
    rid = conn.execute("INSERT INTO eval.report (kind, code_version, body) VALUES ('layout_routing', %s, %s) RETURNING report_id",
                       (code_version(), Jsonb(json.loads(json.dumps(rep, default=str))))).fetchone()[0]
    rep["report_id"], rep["seconds"] = rid, round(time.time() - t, 1)
    print(json.dumps(rep, indent=1, default=str))
    return 0


def cmd_route(conn) -> int:
    t = time.time()
    out = layout.route_documents(conn, RAW)
    out["seconds"] = round(time.time() - t, 1)
    print(json.dumps(out, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["load-fatura", "train-router", "eval-router", "route"])
    args = ap.parse_args()
    with connect() as conn:
        conn.autocommit = True
        migrate(conn)
        return {"load-fatura": cmd_load_fatura, "train-router": cmd_train_router,
                "eval-router": cmd_eval_router, "route": cmd_route}[args.command](conn)


if __name__ == "__main__":
    sys.exit(main())
