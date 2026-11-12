"""How forecasts are scored, the way the M5 competition scored them, plus what a planner reads.

RMSSE: root mean squared error over the horizon, divided by the series' own one-step naive error in
training, so a slow mover and a fast mover count alike. Weighted by each series' dollar sales in the last
52 training weeks (M5's WRMSSE idea), because missing a top seller costs more than missing a slow one.
Demand classes follow Syntetos-Boylan: how often a series sells (ADI) and how much sizes vary (CV2).
"""

from __future__ import annotations

import numpy as np

ADI_CUT, CV2_CUT = 1.32, 0.49
CLASSES = ("smooth", "erratic", "intermittent", "lumpy")


def scale(train: np.ndarray) -> np.ndarray:
    """Mean squared one-step change over observed consecutive training weeks (NaN where none)."""
    d = np.diff(train, axis=1)
    with np.errstate(invalid="ignore"):
        return np.nanmean(d ** 2, axis=1)


def rmsse(actual: np.ndarray, fc: np.ndarray, sc: np.ndarray) -> np.ndarray:
    """Per series; NaN where the horizon has no observed week or the scale is zero or missing."""
    err = (actual - fc) ** 2
    with np.errstate(invalid="ignore", divide="ignore"):
        mse = np.nanmean(np.where(np.isnan(actual), np.nan, err), axis=1)
        out = np.sqrt(mse / sc)
    out[~np.isfinite(out)] = np.nan
    return out


def mase(actual: np.ndarray, fc: np.ndarray, train: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        mae = np.nanmean(np.abs(np.where(np.isnan(actual), np.nan, actual - fc)), axis=1)
        s = np.nanmean(np.abs(np.diff(train, axis=1)), axis=1)
        out = mae / s
    out[~np.isfinite(out)] = np.nan
    return out


def weights(train_units: np.ndarray, train_price: np.ndarray, last: int = 52) -> np.ndarray:
    dollars = np.nansum(train_units[:, -last:] * train_price[:, -last:], axis=1)
    tot = dollars.sum()
    return dollars / tot if tot > 0 else np.full(len(dollars), 1 / len(dollars))


def demand_class(train: np.ndarray) -> np.ndarray:
    obs = ~np.isnan(train)
    pos = obs & (np.nan_to_num(train) > 0)
    n_obs, n_pos = obs.sum(1), pos.sum(1)
    with np.errstate(invalid="ignore", divide="ignore"):
        adi = n_obs / n_pos
        sizes = np.where(pos, train, np.nan)
        cv2 = (np.nanstd(sizes, axis=1) / np.nanmean(sizes, axis=1)) ** 2
    out = np.where(adi < ADI_CUT, np.where(cv2 < CV2_CUT, "smooth", "erratic"),
                   np.where(cv2 < CV2_CUT, "intermittent", "lumpy"))
    out = np.where(n_pos == 0, "no_sales", out)
    return out.astype("<U12")


def summary(actual: np.ndarray, fc: np.ndarray, train: np.ndarray, w: np.ndarray, cls: np.ndarray) -> dict:
    sc = scale(train)
    r = rmsse(actual, fc, sc)
    ok = ~np.isnan(r)
    ww = w * ok
    out = {"wrmsse": float(np.nansum(ww * np.nan_to_num(r)) / ww.sum()) if ww.sum() else None,
           "mean_rmsse": float(np.nanmean(r)), "mase": float(np.nanmean(mase(actual, fc, train))),
           "series_scored": int(ok.sum())}
    with np.errstate(invalid="ignore"):
        a = np.where(np.isnan(actual), np.nan, actual)
        out["bias"] = float((np.nansum(np.where(np.isnan(a), np.nan, fc)) - np.nansum(a)) / np.nansum(a))
    out["by_class"] = {c: (float(np.nanmean(r[(cls == c) & ok])) if ((cls == c) & ok).any() else None) for c in CLASSES}
    return out
