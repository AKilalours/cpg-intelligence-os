"""Fetch the real datasets listed in cpgos/datasets/registry.yaml.

Usage:
    python scripts/download_data.py --list
    python scripts/download_data.py m5 open_food_facts

Each dataset must first be accepted in data/license_acceptance.yaml.
Gated datasets (DocILE, dunnhumby, USDA, CUAD) print exact manual steps.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cpgos.preflight import require_packages  # noqa: E402  (stdlib only)

require_packages({"yaml": "pyyaml"})

from cpgos.datasets.registry import (  # noqa: E402
    LicenseNotAcceptedError,
    load_registry,
    pin_or_verify,
    require_license_accepted,
)


def fetch_http(spec) -> list[Path]:
    url = spec.raw["url"]
    spec.raw_dir.mkdir(parents=True, exist_ok=True)
    name = url.split("/")[-1].split("?")[0]
    dest = spec.raw_dir / name
    if not dest.exists():
        tmp = dest.with_suffix(dest.suffix + ".part")
        print(f"  downloading {url}")
        with urllib.request.urlopen(url, timeout=60) as r, tmp.open("wb") as f:
            shutil.copyfileobj(r, f)
        tmp.rename(dest)  # atomic: a half-downloaded file never looks complete
    if spec.raw.get("archive") != "zip":
        return [dest]
    # Extract only the files the project uses, each atomically.
    wanted = spec.raw.get("files")
    out = [dest]
    with zipfile.ZipFile(dest) as z:
        names = wanted or [n for n in z.namelist() if not n.endswith("/")]
        missing = [n for n in names if n not in z.namelist()]
        if missing:
            raise RuntimeError(f"archive is missing expected files: {missing}")
        for n in names:
            target = spec.raw_dir / Path(n).name
            if not target.exists():
                part = target.with_suffix(target.suffix + ".part")
                with z.open(n) as src, part.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                part.rename(target)
            out.append(target)
    return out


HF_API = "https://huggingface.co/api/datasets/{repo}/tree/{rev}/{path}"
HF_FILE = "https://huggingface.co/datasets/{repo}/resolve/{rev}/{path}"


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_huggingface(spec, opener=urllib.request.urlopen) -> list[Path]:
    """Download a Hugging Face dataset folder. Every file is checked against the SHA-256 that
    Hugging Face publishes for it (its LFS object id) before it is kept, then pinned locally."""
    import json
    repo, rev, folder = spec.raw["repo"], spec.raw.get("revision", "main"), spec.raw.get("path", "data")
    with opener(HF_API.format(repo=repo, rev=rev, path=folder), timeout=60) as r:
        listing = json.loads(r.read())
    files = [e for e in listing if e.get("type") == "file"]
    if not files:
        raise RuntimeError(f"no files listed under {repo}/{folder}")
    out = []
    for e in files:
        dest = spec.raw_dir / e["path"]
        want = (e.get("lfs") or {}).get("oid")
        if dest.exists() and (want is None or _sha256(dest) == want):
            out.append(dest)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        print(f"  downloading {e['path']} ({e.get('size', 0) / 1e6:.1f} MB)")
        with opener(HF_FILE.format(repo=repo, rev=rev, path=e["path"]), timeout=300) as r, tmp.open("wb") as f:
            shutil.copyfileobj(r, f)
        if want is not None and _sha256(tmp) != want:
            tmp.unlink()
            raise RuntimeError(f"{e['path']}: checksum does not match the one Hugging Face publishes; not kept")
        tmp.rename(dest)
        out.append(dest)
    return out


def fetch_kaggle(spec) -> list[Path]:
    if shutil.which("kaggle") is None:
        raise RuntimeError("Install the Kaggle CLI (pip install kaggle) and add ~/.kaggle/kaggle.json")
    comp = spec.raw["kaggle_competition"]
    spec.raw_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["kaggle", "competitions", "download", "-c", comp, "-p", str(spec.raw_dir)], check=True)
    for z in spec.raw_dir.glob("*.zip"):
        shutil.unpack_archive(z, spec.raw_dir)
    wanted = spec.raw.get("files")
    files = [spec.raw_dir / f for f in wanted] if wanted else sorted(spec.raw_dir.glob("*.csv"))
    missing = [f for f in files if not f.exists()]
    if missing:
        raise RuntimeError(f"Expected files missing after download: {missing}")
    return files


def manual_steps(spec) -> list[Path]:
    spec.raw_dir.mkdir(parents=True, exist_ok=True)
    files = [p for p in spec.raw_dir.iterdir() if p.is_file()]
    if not files:
        print(f"  MANUAL: open {spec.landing_page}, complete access steps, "
              f"and place the files in {spec.raw_dir}/ then re-run.")
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("keys", nargs="*")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    reg = load_registry()

    if args.list or not args.keys:
        for k, s in reg.items():
            flag = "" if s.license_verified else "  [check license]"
            print(f"{k:28} {s.method:7} {s.origin:24} {s.title}{flag}")
        return 0

    failed = False
    for key in args.keys:
        spec = reg[key]
        print(f"[{key}] {spec.title}")
        try:
            require_license_accepted(spec)
            if spec.method == "http":
                files = fetch_http(spec)
            elif spec.method == "kaggle":
                files = fetch_kaggle(spec)
            elif spec.method == "huggingface":
                files = fetch_huggingface(spec)
            elif spec.method == "bundled":
                files = [Path(__file__).resolve().parents[1] / spec.raw["file"]]
            else:
                files = manual_steps(spec)
            for f in files:
                print(f"  pinned {f.name} sha256={pin_or_verify(key, f)[:12]}...")
        except LicenseNotAcceptedError as e:
            print(f"  BLOCKED: {e}")
            failed = True
        except Exception as e:  # report and continue with other datasets
            print(f"  FAILED: {e}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
