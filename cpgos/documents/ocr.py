"""OCR with Tesseract: words, their boxes, and their confidence, cached per image and engine.

Choices, each measured on fit-layout training invoices only:
- Pages are enlarged 2x before OCR. FATURA pages are 72 dpi; Tesseract is
  built for about 300. On 40 training invoices, field values read exactly
  rose from 87.3% (1x) to 98.4% (2x); 3x gave 97.9% and was slower.
- Each Tesseract process is limited to one thread (OMP_THREAD_LIMIT=1).
  Tesseract spreads one page over every core; running several pages in
  parallel without the limit oversubscribed the CPU so badly that a 40-page
  batch did not finish in three minutes. One thread per process, one process
  per core, is both faster and predictable.
- Results are cached by image hash and engine id (Tesseract version, page
  mode, scale), so OCR runs once per image per engine and a different
  Tesseract version never reuses another's output.

Boxes are page fractions [x0, y0, x1, y1] from the top-left, the same
convention as the answer key.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image

SCALE = 2
PSM = 3                      # automatic page segmentation: keeps reading order within blocks
CACHE_ROOT = Path(__file__).resolve().parents[2] / "data" / "processed" / "ocr"


class OcrUnavailable(RuntimeError):
    pass


@dataclass
class Word:
    text: str
    conf: float                      # Tesseract word confidence, 0 to 100
    bbox: list[float]                # page fractions, top-left origin
    line: str                        # "block.paragraph.line": words sharing it are one printed line


@lru_cache(maxsize=1)
def tesseract_version() -> str:
    exe = shutil.which("tesseract")
    if exe is None:
        raise OcrUnavailable("Tesseract is not installed. Mac: brew install tesseract; Linux: apt install tesseract-ocr")
    out = subprocess.run([exe, "--version"], capture_output=True, text=True)
    m = re.search(r"tesseract\s+v?(\d+\.\d+\.\d+)", (out.stdout + out.stderr).lower())
    if not m:
        raise OcrUnavailable(f"could not read the Tesseract version from: {(out.stdout + out.stderr)[:200]!r}")
    return m.group(1)


def engine_id() -> str:
    return f"tesseract-{tesseract_version()}-psm{PSM}-x{SCALE}"


def _parse_tsv(tsv: str, width: int, height: int) -> list[Word]:
    words = []
    for r in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (r.get("text") or "").strip()
        if r.get("level") != "5" or not text:
            continue
        left, top, w, h = (int(r[k]) for k in ("left", "top", "width", "height"))
        words.append(Word(text, float(r["conf"]),
                          [round(left / width, 5), round(top / height, 5),
                           round((left + w) / width, 5), round((top + h) / height, 5)],
                          f'{r["block_num"]}.{r["par_num"]}.{r["line_num"]}'))
    return words


def run_tesseract(img: Image.Image) -> list[Word]:
    exe = shutil.which("tesseract")
    if exe is None:
        tesseract_version()                      # raises with install instructions
    big = img.convert("L")
    if SCALE != 1:
        big = big.resize((big.width * SCALE, big.height * SCALE), Image.LANCZOS)
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "page.png"
        big.save(p)
        env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
        out = subprocess.run([exe, str(p), "stdout", "--psm", str(PSM), "tsv"], capture_output=True, text=True, env=env)
    if out.returncode != 0:
        raise RuntimeError(f"tesseract failed: {out.stderr[:300]}")
    return _parse_tsv(out.stdout, big.width, big.height)


def _cache_path(sha256: str) -> Path:
    return CACHE_ROOT / engine_id() / sha256[:2] / f"{sha256}.json"


def ocr_image(path: Path, sha256: str) -> list[Word]:
    """OCR words for one image, from cache when this engine has seen this exact image."""
    cp = _cache_path(sha256)
    if cp.is_file():
        return [Word(**w) for w in json.loads(cp.read_text())]
    with Image.open(path) as im:
        words = run_tesseract(im)
    cp.parent.mkdir(parents=True, exist_ok=True)
    tmp = cp.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(w) for w in words]))
    tmp.replace(cp)                              # atomic: an interrupted run never leaves half a file
    return words


def ocr_many(items: list[tuple[Path, str]], workers: int | None = None) -> list[list[Word]]:
    """OCR many images in parallel, one single-threaded Tesseract per core."""
    workers = workers or max(1, (os.cpu_count() or 2))
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(lambda it: ocr_image(*it), items))


def lines(words: list[Word]) -> list[tuple[str, list[Word]]]:
    """Printed lines in reading order: (text, words)."""
    groups: dict[str, list[Word]] = {}
    for w in words:
        groups.setdefault(w.line, []).append(w)
    out = []
    for ws in groups.values():
        ws.sort(key=lambda w: w.bbox[0])
        out.append((" ".join(w.text for w in ws), ws))
    out.sort(key=lambda t: (min(w.bbox[1] for w in t[1]), min(w.bbox[0] for w in t[1])))
    return out
