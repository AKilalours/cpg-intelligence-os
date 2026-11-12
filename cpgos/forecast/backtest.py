"""Rolling-origin backtests and best-fit selection.

At each origin every method forecasts the next 8 weeks from history before the origin only. Best-fit
picks, per series, the method with the lowest RMSSE on earlier origins and is judged on a later origin it
never saw, so the selection is tested the way it would be used.
"""

from __future__ import annotations

import time

import warnings

import numpy as np

warnings.filterwarnings("ignore", message="Mean of empty slice")

from cpgos.forecast import gbm, metrics, models
from cpgos.forecast.data import HORIZON, Weekly

COMBO = ("ses", "tsb", "lgbm")   # a plain average of three different kinds of method


def forecasts_at(w: Weekly, o: int, with_gbm: bool = True, log=print, cache_dir=None) -> dict[str, np.ndarray]:
    hist = w.upto(o)
    out = {}
    for name, f in models.LOCAL.items():
        out[name] = f(hist.units, HORIZON)
    if with_gbm:
        t = time.time()
        cached = cache_dir / f"lgbm_{gbm.VERSION}_{w.weeks[o]}.npy" if cache_dir else None
        out["lgbm"] = np.load(cached) if cached and cached.exists() else gbm.lgbm(hist, o)
        log(f"   lgbm at origin {w.weeks[o]}: {time.time() - t:.0f}s")
        out["combo"] = np.mean([out[k] for k in COMBO], axis=0)
    return out


def per_series_rmsse(w: Weekly, o: int, fcs: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    train, actual = w.units[:, :o], w.units[:, o:o + HORIZON]
    sc = metrics.scale(train)
    return {k: metrics.rmsse(actual, f, sc) for k, f in fcs.items()}


def best_fit(errors_by_origin: list[dict[str, np.ndarray]], names: list[str]) -> np.ndarray:
    """Index into names of each series' lowest mean RMSSE over the given origins (ties: earlier name).
    A series with no scored origin gets the first name."""
    m = np.stack([np.nanmean(np.stack([e[k] for e in errors_by_origin]), axis=0) for k in names])
    m = np.where(np.isnan(m), np.inf, m)
    return np.argmin(m, axis=0)


def best_by_class(errors_by_origin, names, cls: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Per demand class, the method with the lowest weighted mean RMSSE over the given origins; every series
    in the class uses it. Far fewer choices than per series, so far less room to fit noise."""
    choice = np.zeros(len(cls), dtype=int)
    for c in np.unique(cls):
        m = cls == c
        best, best_v = 0, np.inf
        for j, k in enumerate(names):
            e = np.nanmean(np.stack([o[k][m] for o in errors_by_origin]), axis=0)
            ok = ~np.isnan(e)
            v = np.sum(w[m][ok] * e[ok]) / max(np.sum(w[m][ok]), 1e-12) if ok.any() else np.inf
            if v < best_v:
                best, best_v = j, v
        choice[m] = best
    return choice


def best_with_margin(errors_by_origin, names, default: str, margin: float = 0.15) -> np.ndarray:
    """Per series, leave the single best method only for one that beat it by at least `margin` (relative)
    on every earlier origin: a series must earn its own method, as a lane earns autonomy."""
    d = names.index(default)
    choice = np.full(len(errors_by_origin[0][default]), d)
    best_gain = np.zeros(len(choice))
    for j, k in enumerate(names):
        if k == default:
            continue
        with np.errstate(invalid="ignore"):
            wins = np.all(np.stack([o[k] <= (1 - margin) * o[default] for o in errors_by_origin]), axis=0)
            gain = np.nanmean(np.stack([1 - o[k] / o[default] for o in errors_by_origin]), axis=0)
        better = wins & (np.nan_to_num(gain) > best_gain)
        choice = np.where(better, j, choice)
        best_gain = np.where(better, gain, best_gain)
    return choice


def pick(fcs: dict[str, np.ndarray], names: list[str], choice: np.ndarray) -> np.ndarray:
    stack = np.stack([fcs[k] for k in names])
    return stack[choice, np.arange(stack.shape[1])]


def score_all(w: Weekly, o: int, fcs: dict[str, np.ndarray]) -> dict[str, dict]:
    train, price, actual = w.units[:, :o], w.price[:, :o], w.units[:, o:o + HORIZON]
    wts, cls = metrics.weights(train, price), metrics.demand_class(train)
    return {k: metrics.summary(actual, f, train, wts, cls) for k, f in fcs.items()}
