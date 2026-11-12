"""Forecasting methods, vectorized across all series. Each takes the history (series x weeks, NaN where
an item was not stocked) and returns forecasts for the next h weeks. A NaN week is skipped, never read as
zero: "not on the shelf" is not "sold nothing".

Baselines: naive, seasonal naive (same week last year), moving averages. Smoothing: simple exponential
smoothing with its rate chosen per series on its own history. Intermittent demand: Croston, its
bias-corrected SBA, and TSB (which lets the chance of a sale decay when sales stop, so a dying item's
forecast falls instead of staying flat).
"""

from __future__ import annotations

import numpy as np

SES_GRID = (0.05, 0.1, 0.2, 0.3, 0.5)


def _last_obs(H: np.ndarray) -> np.ndarray:
    out = np.full(H.shape[0], np.nan)
    for t in range(H.shape[1]):
        v = H[:, t]
        out = np.where(np.isnan(v), out, v)
    return out


def _flat(v: np.ndarray, h: int) -> np.ndarray:
    return np.repeat(np.nan_to_num(v)[:, None], h, axis=1)


def naive(H, h):
    return _flat(_last_obs(H), h)


def moving_average(H, h, k):
    return _flat(np.nanmean(H[:, -k:], axis=1) if H.shape[1] else np.zeros(H.shape[0]), h)


def ma4(H, h):
    with np.errstate(invalid="ignore"):
        return moving_average(H, h, 4)


def ma13(H, h):
    with np.errstate(invalid="ignore"):
        return moving_average(H, h, 13)


def seasonal_naive(H, h, season=52):
    T = H.shape[1]
    out = np.empty((H.shape[0], h))
    fb = ma13(H, 1)[:, 0]
    for j in range(h):
        k = T + j - season
        v = H[:, k] if 0 <= k < T else np.full(H.shape[0], np.nan)
        out[:, j] = np.where(np.isnan(v), fb, v)
    return out


def _ses_run(H, alpha):
    """One-step SES over observed weeks; returns final level and in-sample squared error sum and count."""
    n = H.shape[0]
    level = np.full(n, np.nan)
    sse, cnt = np.zeros(n), np.zeros(n)
    a = alpha if np.ndim(alpha) else np.full(n, alpha)
    for t in range(H.shape[1]):
        y = H[:, t]
        obs = ~np.isnan(y)
        has = obs & ~np.isnan(level)
        e = np.where(has, y - np.nan_to_num(level), 0.0)
        sse += e ** 2
        cnt += has
        level = np.where(obs, np.where(np.isnan(level), y, level + a * (y - np.nan_to_num(level))), level)
    return level, sse, cnt


def ses(H, h):
    """Simple exponential smoothing (ETS(A,N,N)); rate per series from a grid, by in-sample one-step error."""
    best_err = np.full(H.shape[0], np.inf)
    best_alpha = np.full(H.shape[0], SES_GRID[0])
    for a in SES_GRID:
        _, sse, cnt = _ses_run(H, a)
        with np.errstate(invalid="ignore", divide="ignore"):
            mse = np.where(cnt > 0, sse / cnt, np.inf)
        better = mse < best_err
        best_err, best_alpha = np.where(better, mse, best_err), np.where(better, a, best_alpha)
    level, _, _ = _ses_run(H, best_alpha)
    return _flat(np.maximum(level, 0), h)


def _croston_state(H, alpha):
    n = H.shape[0]
    z = np.full(n, np.nan)      # demand size when it happens
    p = np.full(n, np.nan)      # weeks between sales
    q = np.ones(n)              # weeks since the last sale
    for t in range(H.shape[1]):
        y = H[:, t]
        obs = ~np.isnan(y)
        sale = obs & (np.nan_to_num(y) > 0)
        first = sale & np.isnan(z)
        upd = sale & ~first
        z = np.where(first, y, np.where(upd, z + alpha * (y - z), z))
        p = np.where(first, q, np.where(upd, p + alpha * (q - p), p))
        q = np.where(sale, 1, np.where(obs, q + 1, q))
    return z, p


def croston(H, h, alpha=0.1):
    z, p = _croston_state(H, alpha)
    with np.errstate(invalid="ignore", divide="ignore"):
        return _flat(np.where(np.isnan(z), 0, z / p), h)


def sba(H, h, alpha=0.1):
    return croston(H, h, alpha) * (1 - alpha / 2)


def tsb(H, h, alpha=0.1, beta=0.1):
    n = H.shape[0]
    z = np.full(n, np.nan)      # size
    pr = np.full(n, np.nan)     # probability of a sale in a week
    for t in range(H.shape[1]):
        y = H[:, t]
        obs = ~np.isnan(y)
        sale = obs & (np.nan_to_num(y) > 0)
        pr = np.where(obs, np.where(np.isnan(pr), sale.astype(float), pr + beta * (sale - np.nan_to_num(pr))), pr)
        z = np.where(sale, np.where(np.isnan(z), y, z + alpha * (y - np.nan_to_num(z))), z)
    return _flat(np.nan_to_num(z) * np.nan_to_num(pr), h)


LOCAL = {"naive": naive, "seasonal_naive": seasonal_naive, "ma4": ma4, "ma13": ma13, "ses": ses,
         "croston": croston, "sba": sba, "tsb": tsb}
