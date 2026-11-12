"""Is one method really better than another? A paired bootstrap over series.

Both methods forecast the same series at the same origin, so their per-series errors are paired. Resampling
series (with each series' dollar weight) gives a 95% interval for the weighted RMSSE difference. An
interval that excludes zero is a real difference on this data; one that straddles zero is not.
"""

from __future__ import annotations

import numpy as np


def weighted(r: np.ndarray, w: np.ndarray) -> float:
    ok = ~np.isnan(r)
    return float(np.sum(w[ok] * r[ok]) / np.sum(w[ok]))


def paired_bootstrap(ra: np.ndarray, rb: np.ndarray, w: np.ndarray, n: int = 2000, seed: int = 0) -> dict:
    """Weighted RMSSE of a minus b, with a 95% bootstrap interval. Negative favours a."""
    ok = ~np.isnan(ra) & ~np.isnan(rb)
    ra, rb, w = ra[ok], rb[ok], w[ok]
    rng = np.random.default_rng(seed)
    diff = float(np.sum(w * ra) / w.sum() - np.sum(w * rb) / w.sum())
    k = len(ra)
    boots = np.empty(n)
    for i in range(n):
        idx = rng.integers(0, k, k)
        ww = w[idx]
        boots[i] = np.sum(ww * ra[idx]) / ww.sum() - np.sum(ww * rb[idx]) / ww.sum()
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"difference": round(diff, 4), "ci95": [round(float(lo), 4), round(float(hi), 4)],
            "relative": round(diff / (np.sum(w * rb) / w.sum()), 4), "series": int(k)}
