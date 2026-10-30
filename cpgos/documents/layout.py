"""Layout routing: recognise a known invoice layout from the page image, or say "new".

Analogy: a mail clerk recognises a regular supplier's envelope at a glance and
sends it down the fast lane; an unfamiliar envelope is opened carefully. Here
the "glance" is an ink fingerprint: the page shrunk to a small grid of cell
darkness. Invoices from one layout share a fingerprint even when the words
differ, because the boxes, logos, and tables sit in the same places.

Why it matters: a known layout can be read by the cheap path (the field
positions are already known); a new layout escalates to the models.

Design decision, measured on fit-layout dev and calibration data only:
cropping each page to its printed content before fingerprinting raised
scan-like known-layout recall from 621 to 779 of 800, but sent 482 of 1,600
scan-like calibration (new) invoices to a known layout. A new supplier's
invoice read with another layout's field positions is the dangerous failure,
so cropping was rejected. The cost of that choice is that scanned copies of
known layouts escalate more often.

Rules that keep the evaluation honest:
- Fingerprints are learned only from the fit layouts' training invoices.
- The grid size and the "new" threshold are chosen on the calibration split
  (8 layouts never used for fitting) plus held-out invoices of known layouts.
- The holdout layouts are scored once, after both choices are frozen.
- Routing reads only the image, never eval.*.
"""

from __future__ import annotations

import hashlib
import io
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import psycopg
from PIL import Image, ImageFilter

MODEL_VERSION = "layout-ink-grid-1.0.0"
GRID_CANDIDATES = ((12, 17), (24, 34), (48, 68))
TARGET_FALSE_NEW = 0.01          # at most 1% of known-layout invoices may be called "new" on calibration


def signature(img: Image.Image, grid: tuple[int, int]) -> np.ndarray:
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


@dataclass
class Router:
    grid: tuple[int, int]
    threshold: float
    keys: list[str]
    centroids: np.ndarray            # (n_layouts, cells)
    n_train: list[int]
    version: str = MODEL_VERSION
    trained_on: dict = field(default_factory=dict)

    def route(self, img: Image.Image) -> tuple[str | None, float, float]:
        """(layout key or None for new, distance to nearest layout, margin to the second nearest)."""
        return self.route_signatures(signature(img, self.grid)[None])[0]

    def route_signatures(self, X: np.ndarray) -> list[tuple[str | None, float, float]]:
        d = np.sqrt(((X[:, None, :] - self.centroids[None]) ** 2).sum(-1))
        order = np.argsort(d, axis=1)
        out = []
        for i in range(len(X)):
            best, second = d[i, order[i, 0]], d[i, order[i, 1]] if d.shape[1] > 1 else np.inf
            key = self.keys[order[i, 0]] if best <= self.threshold else None
            out.append((key, float(best), float(second - best)))
        return out


def _rows(conn, where: str):
    return conn.execute(f"""SELECT s.source_document_id, s.storage_key, m.layout_id, m.split_unseen, m.split_seen
                            FROM eval.doc_meta m JOIN source_document s USING (source_document_id)
                            WHERE m.dataset_key = 'fatura' AND {where} ORDER BY m.doc_key""").fetchall()


def _sigs(raw_root: Path, rows, grids, rng: random.Random | None = None) -> dict:
    out = {g: [] for g in grids}
    for r in rows:
        img = load_image(raw_root, r[1])
        if rng is not None:
            img = perturb(img, rng)
        for g in grids:
            out[g].append(signature(img, g))
    return {g: np.stack(v) if v else np.zeros((0, g[0] * g[1]), np.float32) for g, v in out.items()}


def _fit(X: np.ndarray, labels: list[int]) -> tuple[list[str], np.ndarray, list[int]]:
    keys = sorted(set(labels))
    lab = np.array(labels)
    cents = np.stack([X[lab == k].mean(0) for k in keys])
    return [f"fatura_L{k:02d}" for k in keys], cents, [int((lab == k).sum()) for k in keys]


def auroc(pos: np.ndarray, neg: np.ndarray) -> float | None:
    """Probability a random 'new' invoice is farther than a random known one (ties count half)."""
    if len(pos) == 0 or len(neg) == 0:
        return None
    s = np.concatenate([pos, neg])
    order = s.argsort(kind="mergesort")
    ranks = np.empty(len(s))
    ranks[order] = np.arange(1, len(s) + 1)
    for v in np.unique(s):                          # average ranks over ties
        m = s == v
        ranks[m] = ranks[m].mean()
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def train(conn: psycopg.Connection, raw_root: Path) -> tuple[Router, dict]:
    """Learn fingerprints from fit layouts; choose grid and threshold on calibration only.

    Known-layout invoices are split by FATURA's familiar-layout split: 'train'
    builds fingerprints, 'dev' sets the threshold, 'test' is kept for the final
    score. Calibration layouts are the 'new' examples for choosing."""
    fit_train = _rows(conn, "m.split_unseen = 'fit' AND m.split_seen = 'train'")
    fit_dev = _rows(conn, "m.split_unseen = 'fit' AND m.split_seen = 'dev'")
    calib = _rows(conn, "m.split_unseen = 'calibration'")
    if not fit_train or not fit_dev or not calib:
        raise RuntimeError("FATURA answer key not loaded; run: make load-fatura")
    S_tr, S_dev, S_cal = _sigs(raw_root, fit_train, GRID_CANDIDATES), _sigs(raw_root, fit_dev, GRID_CANDIDATES), \
        _sigs(raw_root, calib, GRID_CANDIDATES)
    choices = []
    for g in GRID_CANDIDATES:
        keys, cents, n = _fit(S_tr[g], [r[2] for r in fit_train])
        probe = Router(g, float("inf"), keys, cents, n)
        d_known = np.array([x[1] for x in probe.route_signatures(S_dev[g])])
        d_new = np.array([x[1] for x in probe.route_signatures(S_cal[g])])
        thr = float(np.quantile(d_known, 1 - TARGET_FALSE_NEW))
        caught = float((d_new > thr).mean())
        choices.append({"grid": list(g), "auroc_calibration": auroc(d_new, d_known), "threshold": thr,
                        "new_caught_calibration": caught, "keys": keys, "cents": cents, "n": n})
    best = max(choices, key=lambda c: (c["auroc_calibration"], c["new_caught_calibration"], -c["grid"][0]))
    router = Router(tuple(best["grid"]), best["threshold"], best["keys"], best["cents"], best["n"],
                    trained_on={"fingerprints": "fit layouts, familiar split 'train'",
                                "threshold": f"{int(TARGET_FALSE_NEW * 100)}% false-new on fit layouts' 'dev'",
                                "grid_choice": "highest AUROC on calibration layouts",
                                "fit_layouts": len(best["keys"]), "train_invoices": len(fit_train)})
    selection = [{k: v for k, v in c.items() if k not in ("keys", "cents", "n")} for c in choices]
    return router, {"selection": selection}


def evaluate(conn: psycopg.Connection, raw_root: Path, router: Router, seed: int = 7) -> dict:
    """Final scores, after the grid and threshold are frozen."""
    known = _rows(conn, "m.split_unseen = 'fit' AND m.split_seen = 'test'")
    holdout = _rows(conn, "m.split_unseen = 'holdout'")
    g = (router.grid,)
    Sk = _sigs(raw_root, known, g)[router.grid]
    Sh = _sigs(raw_root, holdout, g)[router.grid]
    Sp = _sigs(raw_root, known, g, rng=random.Random(seed))[router.grid]
    Shp = _sigs(raw_root, holdout, g, rng=random.Random(seed + 1))[router.grid]
    rk, rh, rp = router.route_signatures(Sk), router.route_signatures(Sh), router.route_signatures(Sp)
    rhp = router.route_signatures(Shp)
    truth = [f"fatura_L{r[2]:02d}" for r in known]

    def known_stats(res):
        right = sum(k == t for (k, _, _), t in zip(res, truth))
        wrong = sum(k is not None and k != t for (k, _, _), t in zip(res, truth))
        new = sum(k is None for k, _, _ in res)
        return {"n": len(res), "correct_layout": right, "wrong_layout": wrong, "called_new": new}

    return {
        "router": {"version": router.version, "grid": list(router.grid), "threshold": round(router.threshold, 4),
                   "layouts": len(router.keys), "trained_on": router.trained_on},
        "known_layouts_clean": known_stats(rk),
        "known_layouts_scan_like": {**known_stats(rp),
                                    "perturbation": "rotation +-2 deg, shift +-12 px, blur 0.3-1.0, JPEG q35-70"},
        "new_layouts_holdout": {"n": len(rh), "called_new": sum(k is None for k, _, _ in rh),
                                "wrongly_matched_to_known": sum(k is not None for k, _, _ in rh)},
        "new_layouts_holdout_scan_like": {"n": len(rhp), "called_new": sum(k is None for k, _, _ in rhp),
                                          "wrongly_matched_to_known": sum(k is not None for k, _, _ in rhp)},
        "auroc_holdout_vs_known": auroc(np.array([x[1] for x in rh]), np.array([x[1] for x in rk])),
    }


def save(conn: psycopg.Connection, router: Router) -> int:
    blob = router.centroids.astype(np.float32)
    digest = hashlib.sha256(blob.tobytes() + json.dumps([router.grid, router.threshold, router.keys]).encode()).hexdigest()
    with conn.transaction():
        row = conn.execute("SELECT layout_model_id FROM layout_model WHERE content_sha256 = %s", (digest,)).fetchone()
        if row:
            return row[0]
        mid = conn.execute(
            """INSERT INTO layout_model (version, grid_w, grid_h, threshold, trained_on, content_sha256)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING layout_model_id""",
            (router.version, router.grid[0], router.grid[1], router.threshold, json.dumps(router.trained_on), digest),
        ).fetchone()[0]
        for k, c, n in zip(router.keys, blob, router.n_train):
            conn.execute("INSERT INTO layout_centroid (layout_model_id, layout_key, n_train, centroid) VALUES (%s, %s, %s, %s)",
                         (mid, k, n, c.tolist()))
    return mid


def load(conn: psycopg.Connection, layout_model_id: int | None = None) -> tuple[int, Router]:
    m = conn.execute(
        """SELECT layout_model_id, version, grid_w, grid_h, threshold, trained_on FROM layout_model
           WHERE %(id)s::bigint IS NULL OR layout_model_id = %(id)s
           ORDER BY layout_model_id DESC LIMIT 1""", {"id": layout_model_id}).fetchone()
    if m is None:
        raise LookupError("no layout model; run: make train-router")
    cents = conn.execute("SELECT layout_key, n_train, centroid FROM layout_centroid WHERE layout_model_id = %s ORDER BY layout_key",
                         (m[0],)).fetchall()
    return m[0], Router((m[2], m[3]), float(m[4]), [c[0] for c in cents],
                        np.array([c[2] for c in cents], dtype=np.float32), [c[1] for c in cents], m[1],
                        m[5] if isinstance(m[5], dict) else json.loads(m[5]))


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
    for sid, key in docs:
        k, dist, margin = router.route(load_image(raw_root, key))
        batch.append((sid, mid, k, round(dist, 5), round(margin, 5) if np.isfinite(margin) else None))
        counts["routed"] += 1
        counts["known" if k else "new"] += 1
    with conn.transaction():
        conn.execute("SET LOCAL ROLE cpgos_engine")
        with conn.cursor().copy(
                "COPY document_route (source_document_id, layout_model_id, layout_key, distance, margin) FROM STDIN") as cp:
            for row in batch:
                cp.write_row(row)
    counts["layout_model_id"] = mid
    return counts
