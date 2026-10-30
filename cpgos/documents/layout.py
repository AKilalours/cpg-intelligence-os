"""Layout routing: recognise a known invoice layout from the page image, or say "new".

Analogy: a person recognises a printed form by its frame (logo, boxes, printed
labels), not by what was written into it, and still recognises it when the
scan is slightly crooked. The router does the same:

1. Ink fingerprint: the page shrunk to a 48 x 68 grid of cell darkness.
2. Stable cells: for each known layout, the cells that barely change between
   its invoices (the frame, and the blank space around it). Only those are
   compared, so changing words, amounts, and table lengths do not count.
3. Small slide: the page may be compared one cell up, down, left, or right
   of where it landed. That re-aligns a shifted scan of a known layout, but
   cannot make a different frame match.
4. Scan-like training: each training invoice is also learned from a copy
   with scan damage (rotation, shift, blur, JPEG), so "stable" means stable
   under real scanning, not only on perfect images.
5. A threshold per layout: some layouts vary more than others.

Why it matters: a known layout can be read by the cheap path; a new layout
escalates to the models. A known invoice called "new" costs money; a new
invoice routed to a known layout produces confident wrong values. The second
is the error to avoid, and the selection rule below puts it first.

Version history, all measured on fit-layout dev and calibration layouts only:
- 1.0.0 whole-page fingerprint: 0 new invoices misrouted, but only 618 of 800
  scan-like known invoices recognised (the rest escalated).
- Cropping to content (rejected): 779/800 recognised but 482 of 1,600 new
  invoices misrouted.
- Whole-page fingerprint + slide (rejected): 757 to 792/800 recognised but
  70 to 454 of 1,600 new invoices misrouted.
- 2.0.0 stable cells + slide + scan-like training + per-layout thresholds:
  chosen by `train` below.

Rules that keep the evaluation honest: fingerprints come only from the fit
layouts' training invoices; every setting and threshold is chosen on
fit-layout dev and the calibration layouts; the holdout layouts are scored
once by `evaluate`; routing reads only the image, never eval.*.
"""

from __future__ import annotations

import hashlib
import io
import json
import random
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path

import numpy as np
import psycopg
from PIL import Image, ImageFilter

MODEL_VERSION = "layout-stable-cells-2.0.0"
GRID = (48, 68)                  # chosen in 1.0.0 on calibration layouts (AUROC 1.000 vs 0.999 at 24 x 34)
CANDIDATES = {"scan_like_training": (False, True), "stable_std": (0.05, 0.1, 0.2), "slide_cells": (0, 1)}
THRESHOLD_QUANTILE = 0.99        # of a layout's own scan-like dev distances
THRESHOLD_SAFETY = 1.25
PERTURB_SEEDS = {"train": 13, "dev": 11, "calibration": 12, "test": 7, "holdout": 8}


def signature(img: Image.Image, grid: tuple[int, int] = GRID) -> np.ndarray:
    """Ink density per grid cell, 0 = white, 1 = black."""
    g = img.convert("L").resize(grid, Image.BOX)
    return (1.0 - np.asarray(g, dtype=np.float32) / 255.0).ravel()


def load_image(raw_root: Path, storage_key: str) -> Image.Image:
    with Image.open(raw_root / storage_key) as im:
        return im.convert("L").copy()


def perturb(img: Image.Image, rng: random.Random) -> Image.Image:
    """A scan-like copy: small rotation, shift, blur, and JPEG recompression."""
    w, h = img.size
    out = img.rotate(rng.uniform(-2.0, 2.0), resample=Image.BILINEAR, fillcolor=255,
                     translate=(rng.randint(-12, 12), rng.randint(-12, 12)))
    out = out.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 1.0)))
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=rng.randint(35, 70))
    buf.seek(0)
    with Image.open(buf) as im:
        return im.convert("L").resize((w, h)).copy()


def _slides(X: np.ndarray, grid: tuple[int, int], s: int):
    """Yield X slid by every (dx, dy) in [-s, s]^2; cells moved off the page become white."""
    w, h = grid
    Q = X.reshape(len(X), h, w)
    for dy, dx in product(range(-s, s + 1), repeat=2):
        Z = np.zeros_like(Q)
        ys, yd = (slice(dy, None), slice(None, h - dy)) if dy >= 0 else (slice(None, dy), slice(-dy, None))
        xs, xd = (slice(dx, None), slice(None, w - dx)) if dx >= 0 else (slice(None, dx), slice(-dx, None))
        Z[:, ys, xs] = Q[:, yd, xd]
        yield Z.reshape(len(X), -1)


def masked_distances(X: np.ndarray, centroids: np.ndarray, masks: np.ndarray, grid: tuple[int, int],
                     slide: int) -> np.ndarray:
    """(n_docs, n_layouts) root-mean-square difference on each layout's stable cells, best over slides."""
    M = masks.astype(np.float32)
    cnt = np.maximum(M.sum(1), 1.0)
    MC, MC2 = M * centroids, (M * centroids ** 2).sum(1)
    best = np.full((len(X), len(centroids)), np.inf, dtype=np.float32)
    for Z in _slides(X.astype(np.float32), grid, slide):
        sq = (Z ** 2) @ M.T - 2.0 * (Z @ MC.T) + MC2[None]
        best = np.minimum(best, np.sqrt(np.maximum(sq, 0.0) / cnt[None]))
    return best


@dataclass
class Router:
    grid: tuple[int, int]
    keys: list[str]
    centroids: np.ndarray            # (n_layouts, cells)
    masks: np.ndarray                # (n_layouts, cells) bool: stable cells
    thresholds: np.ndarray           # (n_layouts,)
    slide: int
    n_train: list[int]
    version: str = MODEL_VERSION
    trained_on: dict = field(default_factory=dict)

    def route(self, img: Image.Image) -> tuple[str | None, float, float]:
        return self.route_signatures(signature(img, self.grid)[None])[0]

    def route_signatures(self, X: np.ndarray) -> list[tuple[str | None, float, float]]:
        """Per document: (layout key, or None for new; distance as a fraction of the nearest layout's
        threshold, so <= 1 means recognised; gap to the second-nearest layout in the same units)."""
        D = masked_distances(X, self.centroids, self.masks, self.grid, self.slide) / self.thresholds[None]
        order = np.argsort(D, axis=1)
        out = []
        for i in range(len(X)):
            best = float(D[i, order[i, 0]])
            gap = float(D[i, order[i, 1]] - best) if D.shape[1] > 1 else float("inf")
            out.append((self.keys[order[i, 0]] if best <= 1.0 else None, best, gap))
        return out


def _rows(conn, where: str):
    return conn.execute(f"""SELECT s.source_document_id, s.storage_key, m.layout_id, m.split_unseen, m.split_seen
                            FROM eval.doc_meta m JOIN source_document s USING (source_document_id)
                            WHERE m.dataset_key = 'fatura' AND {where} ORDER BY m.doc_key""").fetchall()


def _sigs(raw_root: Path, rows, rng: random.Random | None = None) -> np.ndarray:
    out = []
    for r in rows:
        img = load_image(raw_root, r[1])
        out.append(signature(perturb(img, rng) if rng is not None else img))
    return np.stack(out) if out else np.zeros((0, GRID[0] * GRID[1]), np.float32)


def _key(layout_id: int) -> str:
    return f"fatura_L{layout_id:02d}"


def build(X: np.ndarray, labels: list[str], X_dev_scan: np.ndarray, dev_labels: list[str], *,
          stable_std: float, slide: int, grid: tuple[int, int] = GRID) -> Router:
    """Fingerprints and stable cells from training signatures; thresholds from scan-like dev."""
    keys = sorted(set(labels))
    lab = np.array(labels)
    cents = np.stack([X[lab == k].mean(0) for k in keys])
    masks = np.stack([X[lab == k].std(0) < stable_std for k in keys])
    D = masked_distances(X_dev_scan, cents, masks, grid, slide)
    dl = np.array(dev_labels)
    thr = []
    for i, k in enumerate(keys):
        own = D[dl == k, i]
        if len(own) == 0:
            raise ValueError(f"layout {k} has no dev invoices to set its threshold")
        thr.append(float(np.quantile(own, THRESHOLD_QUANTILE)) * THRESHOLD_SAFETY)
    return Router(grid, keys, cents, masks, np.array(thr, dtype=np.float32), slide,
                  [int((lab == k).sum()) for k in keys])


def _known_stats(res, truth) -> dict:
    right = sum(k == t for (k, _, _), t in zip(res, truth))
    wrong = sum(k is not None and k != t for (k, _, _), t in zip(res, truth))
    return {"n": len(res), "correct_layout": right, "wrong_layout": wrong, "called_new": sum(k is None for k, _, _ in res)}


def _new_stats(res) -> dict:
    return {"n": len(res), "called_new": sum(k is None for k, _, _ in res),
            "wrongly_matched_to_known": sum(k is not None for k, _, _ in res)}


def train(conn: psycopg.Connection, raw_root: Path) -> tuple[Router, dict]:
    """Choose settings on calibration, in this order of importance:
    1. fewest new (calibration) invoices routed to a known layout, clean and scan-like;
    2. most scan-like known (dev) invoices recognised;
    3. largest safety margin: how far the closest new invoice stays beyond its nearest threshold."""
    fit_train = _rows(conn, "m.split_unseen = 'fit' AND m.split_seen = 'train'")
    fit_dev = _rows(conn, "m.split_unseen = 'fit' AND m.split_seen = 'dev'")
    calib = _rows(conn, "m.split_unseen = 'calibration'")
    if not fit_train or not fit_dev or not calib:
        raise RuntimeError("FATURA answer key not loaded; run: make load-fatura")
    X_tr = _sigs(raw_root, fit_train)
    X_tr_scan = _sigs(raw_root, fit_train, random.Random(PERTURB_SEEDS["train"]))
    X_dev, X_dev_scan = _sigs(raw_root, fit_dev), _sigs(raw_root, fit_dev, random.Random(PERTURB_SEEDS["dev"]))
    X_cal, X_cal_scan = _sigs(raw_root, calib), _sigs(raw_root, calib, random.Random(PERTURB_SEEDS["calibration"]))
    y_tr, y_dev = [_key(r[2]) for r in fit_train], [_key(r[2]) for r in fit_dev]

    tried = []
    for aug, std, slide in product(*CANDIDATES.values()):
        X = np.concatenate([X_tr, X_tr_scan]) if aug else X_tr
        r = build(X, y_tr + y_tr if aug else y_tr, X_dev_scan, y_dev, stable_std=std, slide=slide)
        dev_c, dev_s = _known_stats(r.route_signatures(X_dev), y_dev), _known_stats(r.route_signatures(X_dev_scan), y_dev)
        rc, rs = r.route_signatures(X_cal), r.route_signatures(X_cal_scan)
        leaks = _new_stats(rc)["wrongly_matched_to_known"] + _new_stats(rs)["wrongly_matched_to_known"]
        margin = min(min(x[1] for x in rc), min(x[1] for x in rs)) - 1.0
        tried.append({"scan_like_training": aug, "stable_std": std, "slide_cells": slide,
                      "calibration_new_misrouted": leaks, "dev_scan_known_recognised": dev_s["correct_layout"],
                      "dev_clean_known_recognised": dev_c["correct_layout"],
                      "dev_known_wrong_layout": dev_c["wrong_layout"] + dev_s["wrong_layout"],
                      "safety_margin": round(margin, 4), "_router": r, "_X": X})
    best = min(tried, key=lambda t: (t["calibration_new_misrouted"] + t["dev_known_wrong_layout"],
                                     -t["dev_scan_known_recognised"], -t["safety_margin"]))
    router = best["_router"]
    router.trained_on = {
        "fingerprints": "fit layouts, familiar split 'train'" + (" + scan-like copies" if best["scan_like_training"] else ""),
        "thresholds": f"per layout: {THRESHOLD_SAFETY} x {THRESHOLD_QUANTILE:.0%} of its own scan-like 'dev' distances",
        "settings_chosen_on": "calibration layouts (new) and fit-layout 'dev' (known)",
        "stable_std": best["stable_std"], "slide_cells": best["slide_cells"],
        "scan_like_training": best["scan_like_training"], "fit_layouts": len(router.keys),
        "train_invoices": len(fit_train)}
    return router, {"tried": [{k: v for k, v in t.items() if not k.startswith("_")} for t in tried]}


def evaluate(conn: psycopg.Connection, raw_root: Path, router: Router) -> dict:
    """Scores after every setting is frozen. Holdout layouts are never read during training."""
    known = _rows(conn, "m.split_unseen = 'fit' AND m.split_seen = 'test'")
    holdout = _rows(conn, "m.split_unseen = 'holdout'")
    truth = [_key(r[2]) for r in known]
    rk = router.route_signatures(_sigs(raw_root, known))
    rp = router.route_signatures(_sigs(raw_root, known, random.Random(PERTURB_SEEDS["test"])))
    rh = router.route_signatures(_sigs(raw_root, holdout))
    rhp = router.route_signatures(_sigs(raw_root, holdout, random.Random(PERTURB_SEEDS["holdout"])))
    return {
        "router": {"version": router.version, "grid": list(router.grid), "layouts": len(router.keys),
                   "slide_cells": router.slide, "trained_on": router.trained_on},
        "known_layouts_clean": _known_stats(rk, truth),
        "known_layouts_scan_like": {**_known_stats(rp, truth),
                                    "perturbation": "rotation +-2 deg, shift +-12 px, blur 0.3-1.0, JPEG q35-70"},
        "new_layouts_holdout": _new_stats(rh),
        "new_layouts_holdout_scan_like": _new_stats(rhp),
        "closest_new_invoice_vs_threshold": round(min(x[1] for x in rh + rhp), 4),
        "auroc_holdout_vs_known": auroc(np.array([x[1] for x in rh + rhp]), np.array([x[1] for x in rk + rp])),
    }


def auroc(pos: np.ndarray, neg: np.ndarray) -> float | None:
    """Probability a random new invoice is farther than a random known one (ties count half)."""
    if len(pos) == 0 or len(neg) == 0:
        return None
    s = np.concatenate([pos, neg])
    order = s.argsort(kind="mergesort")
    ranks = np.empty(len(s))
    ranks[order] = np.arange(1, len(s) + 1)
    for v in np.unique(s):
        m = s == v
        ranks[m] = ranks[m].mean()
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def save(conn: psycopg.Connection, router: Router) -> int:
    cents = router.centroids.astype(np.float32)
    digest = hashlib.sha256(cents.tobytes() + router.masks.astype(np.uint8).tobytes() +
                            router.thresholds.astype(np.float32).tobytes() +
                            json.dumps([router.version, router.grid, router.slide, router.keys]).encode()).hexdigest()
    with conn.transaction():
        row = conn.execute("SELECT layout_model_id FROM layout_model WHERE content_sha256 = %s", (digest,)).fetchone()
        if row:
            return row[0]
        mid = conn.execute(
            """INSERT INTO layout_model (version, grid_w, grid_h, threshold, trained_on, content_sha256, slide_cells)
               VALUES (%s, %s, %s, 1.0, %s, %s, %s) RETURNING layout_model_id""",
            (router.version, router.grid[0], router.grid[1], json.dumps(router.trained_on), digest, router.slide),
        ).fetchone()[0]
        for k, c, m, t, n in zip(router.keys, cents, router.masks, router.thresholds, router.n_train):
            conn.execute("""INSERT INTO layout_centroid (layout_model_id, layout_key, n_train, centroid, stable_mask, threshold)
                            VALUES (%s, %s, %s, %s, %s, %s)""", (mid, k, n, c.tolist(), m.tolist(), float(t)))
    return mid


def load(conn: psycopg.Connection, layout_model_id: int | None = None) -> tuple[int, Router]:
    """Load a stored router. Models from 1.0.0 (no stable cells) load with every cell stable,
    no slide, and their global threshold, so old routing decisions stay reproducible."""
    m = conn.execute(
        """SELECT layout_model_id, version, grid_w, grid_h, threshold, trained_on, slide_cells FROM layout_model
           WHERE %(id)s::bigint IS NULL OR layout_model_id = %(id)s
           ORDER BY layout_model_id DESC LIMIT 1""", {"id": layout_model_id}).fetchone()
    if m is None:
        raise LookupError("no layout model; run: make train-router")
    rows = conn.execute("""SELECT layout_key, n_train, centroid, stable_mask, threshold FROM layout_centroid
                           WHERE layout_model_id = %s ORDER BY layout_key""", (m[0],)).fetchall()
    cents = np.array([r[2] for r in rows], dtype=np.float32)
    masks = np.array([r[3] if r[3] is not None else [True] * cents.shape[1] for r in rows], dtype=bool)
    thr = np.array([r[4] if r[4] is not None else m[4] for r in rows], dtype=np.float32)
    if m[1].startswith("layout-ink-grid-1."):
        # 1.0.0 compared plain Euclidean distance on the whole page against one global threshold.
        cnt = cents.shape[1]
        thr = np.full(len(rows), float(m[4]) / np.sqrt(cnt), dtype=np.float32)
    trained = m[5] if isinstance(m[5], dict) else json.loads(m[5])
    return m[0], Router((m[2], m[3]), [r[0] for r in rows], cents, masks, thr, int(m[6] or 0),
                        [r[1] for r in rows], m[1], trained)


def route_documents(conn: psycopg.Connection, raw_root: Path, layout_model_id: int | None = None,
                    limit: int | None = None) -> dict:
    """Route every unrouted image document as the engine role, which cannot read eval.*."""
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_engine")
        mid, router = load(conn, layout_model_id)
        docs = conn.execute(
            """SELECT source_document_id, storage_key FROM source_document s
               WHERE mime_type LIKE 'image/%%' AND NOT EXISTS (
                   SELECT 1 FROM document_route r WHERE r.source_document_id = s.source_document_id
                   AND r.layout_model_id = %s)
               ORDER BY source_document_id LIMIT %s""", (mid, limit)).fetchall()
    counts = {"routed": 0, "known": 0, "new": 0}
    batch = []
    for start in range(0, len(docs), 256):
        chunk = docs[start:start + 256]
        res = router.route_signatures(np.stack([signature(load_image(raw_root, key), router.grid) for _, key in chunk]))
        for (sid, _), (k, dist, gap) in zip(chunk, res):
            batch.append((sid, mid, k, round(dist, 5), round(gap, 5) if np.isfinite(gap) else None))
            counts["known" if k else "new"] += 1
    counts["routed"] = len(batch)
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_engine")
        with conn.cursor().copy(
                "COPY document_route (source_document_id, layout_model_id, layout_key, distance, margin) FROM STDIN") as cp:
            for row in batch:
                cp.write_row(row)
    counts["layout_model_id"] = mid
    return counts
