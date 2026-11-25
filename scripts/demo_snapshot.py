"""Package what the public demo needs into local_runs/demo/, refusing anything that may not be shared.

Run on the machine with the full database (make demo-snapshot). Produces:
  db.dump        pg_dump of the database (custom format)
  files.tar.gz   published forecast files, reports, and the invoice/receipt images the demo shows
  manifest.json  what is inside, and the checks it passed

Licence guard: the dump is refused if the database holds any dataset that is research-only or needs
registration (DocILE above all), and the image list is built from an allow-list of folders, never a
block-list. Nothing here is uploaded; uploading is a separate command you run yourself.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tarfile
from pathlib import Path

import yaml

from psycopg.rows import dict_row

from cpgos import config, db
from cpgos.documents import review

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "local_runs" / "demo"
RAW = ROOT / "data" / "raw"
SHAREABLE_IMAGE_DIRS = ("fatura/", "cord/")         # CC BY 4.0 both; attribution is on the demo banner
NEVER = {"docile"}                                   # research use only, no redistribution
MAX_IMAGE_BYTES = 1_200_000_000                      # keep the image small enough to build on a free tier
FILES = ["data/processed/forecast/published.npz", "data/processed/forecast/promo_events.npz",
         "data/processed/forecast/lost_sales.npz", "reports"]


def blocked_keys() -> set[str]:
    reg = yaml.safe_load((ROOT / "cpgos" / "datasets" / "registry.yaml").read_text())
    ds = reg.get("datasets", reg)
    out = set(NEVER)
    for key, meta in ds.items():
        lic = str((meta or {}).get("license", "")).lower()
        if "research" in lic or "registration" in lic or "request" in lic:
            out.add(key)
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with db.connect() as conn:
        keys = [r[0] for r in conn.execute("SELECT DISTINCT dataset_key FROM dataset_version ORDER BY 1").fetchall()]
        bad = sorted(set(keys) & blocked_keys())
        if bad:
            print(f"REFUSED: the database holds {bad}, which may not be redistributed. Nothing written.", file=sys.stderr)
            return 1
        rows = conn.execute("SELECT source_document_id, storage_key FROM source_document ORDER BY source_document_id").fetchall()
        conn.row_factory = dict_row
        queue = {it["source_document_id"] for it in review.queue(conn, 500)}
    odd = [k for _, k in rows if not k.startswith(SHAREABLE_IMAGE_DIRS)]
    if any(k.lower().startswith(tuple(NEVER)) for k in odd):
        print("REFUSED: a document from a non-shareable dataset is in source_document.", file=sys.stderr)
        return 1
    # Images: the review queue first (the demo opens on it), then the rest in id order, up to the size cap.
    ordered = sorted(rows, key=lambda r: (r[0] not in queue, r[0]))
    images, total = [], 0
    for _, key in ordered:
        p = RAW / key
        if key.startswith(SHAREABLE_IMAGE_DIRS) and p.is_file():
            size = p.stat().st_size
            if total + size > MAX_IMAGE_BYTES:
                break
            images.append(key); total += size
    print(f"datasets: {keys}\nimages: {len(images):,} of {len(rows):,} documents, {total / 1e6:,.0f} MB")

    dump = OUT / "db.dump"
    port = config.get("CPGOS_DB_PORT", "5432")
    with dump.open("wb") as f:
        subprocess.run(["docker", "compose", "exec", "-T", "db", "pg_dump", "-U", "cpgos", "-Fc", "-Z", "6", "cpgos"],
                       cwd=ROOT, stdout=f, check=True)
    print(f"db.dump: {dump.stat().st_size / 1e6:,.0f} MB (database on port {port})")

    with tarfile.open(OUT / "files.tar.gz", "w:gz") as t:
        for rel in FILES:
            if (ROOT / rel).exists():
                t.add(ROOT / rel, arcname=rel)
        for key in images:
            t.add(RAW / key, arcname=f"data/raw/{key}")
    (OUT / "manifest.json").write_text(json.dumps({
        "datasets": keys, "blocked_checked": sorted(blocked_keys()), "images": len(images),
        "image_bytes": total, "files": FILES}, indent=2))
    print(f"files.tar.gz: {(OUT / 'files.tar.gz').stat().st_size / 1e6:,.0f} MB\nWritten to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
