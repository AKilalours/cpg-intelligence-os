"""An 80% range around each forecast, learned only from development errors.

Errors are standardised by each series' own scale (the root of its mean squared week-to-week change) so a
slow and a fast mover share one table: per demand class and weeks ahead, the 10th and 90th percentiles of
(actual - forecast) / scale over the development origins. The range is forecast + q * scale, never below
zero. Its real coverage is then measured on weeks it never saw; a range is only as honest as that number.
"""

from __future__ import annotations

import numpy as np

from cpgos.forecast import metrics
from cpgos.forecast.data import HORIZON

LO, HI = 0.10, 0.90
CLASSES = metrics.CLASSES + ("no_sales",)


def fit(pairs: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]) -> dict[str, np.ndarray]:
    """pairs: (actual, forecast, scale, class) per development origin. Returns class -> (2, HORIZON)."""
    table = {}
    for c in CLASSES:
        z = [[] for _ in range(HORIZON)]
        for actual, fc, sc, cls in pairs:
            m = (cls == c) & np.isfinite(sc) & (sc > 0)
            s = np.sqrt(sc[m])[:, None]
            r = (actual[m] - fc[m]) / s
            for h in range(HORIZON):
                v = r[:, h]
                z[h].extend(v[np.isfinite(v)].tolist())
        table[c] = np.array([[np.quantile(z[h], q) if z[h] else 0.0 for h in range(HORIZON)] for q in (LO, HI)])
    return table


def apply(fc: np.ndarray, sc: np.ndarray, cls: np.ndarray, table: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    s = np.sqrt(np.nan_to_num(sc))[:, None]
    q = np.stack([table.get(c, table["smooth"]) for c in cls])          # (series, 2, HORIZON)
    return np.maximum(fc + q[:, 0] * s, 0), np.maximum(fc + q[:, 1] * s, 0)


def coverage(actual: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> float:
    ok = ~np.isnan(actual)
    return float(((actual >= lo) & (actual <= hi))[ok].mean())
