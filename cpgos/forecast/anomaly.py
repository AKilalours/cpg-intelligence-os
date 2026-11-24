"""Lost sales: weeks an item was on the shelf and sold far less than it normally does.

A week counts only if the item was stocked (it had a shelf price). Its expected sales are the mean of its
last 8 stocked weeks, and its usual variability is a negative binomial fitted to its last 52 weeks (variance
= mean + mean^2 / r): fast movers vary more than a Poisson allows, and a test that ignores that flags noise.
The score is how surprising the week is: P(sales this low or lower). A very surprising low week is almost
always an out-of-stock, an item missing from the shelf, or a data problem: in each case sales were lost.

Detectors compared (decision 115): a plain rule (zero sales while the item usually sells 3+), a Poisson tail,
and a negative-binomial tail. Each one's threshold is set so it flags 1% of ordinary development weeks; they
are then judged on planted stock-outs they never saw.
"""

from __future__ import annotations

import math

import numpy as np

BASE = 8          # expected sales: mean of the last 8 stocked weeks
DISP = 52         # variability: fitted on the last 52 weeks


def expected(units: np.ndarray, t: int) -> np.ndarray:
    """Mean of each series' last BASE stocked weeks before week t (NaN if fewer than 4). Vectorized: walk
    back from t, taking stocked weeks until each series has BASE of them."""
    n = units.shape[0]
    total, count = np.zeros(n), np.zeros(n)
    for k in range(t - 1, max(-1, t - 27), -1):
        v = units[:, k]
        take = ~np.isnan(v) & (count < BASE)
        total += np.where(take, v, 0.0)
        count += take
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(count >= 4, total / count, np.nan)


def dispersion(units: np.ndarray, t: int) -> np.ndarray:
    """Negative-binomial size r per series by the method of moments over the last DISP weeks; large r is
    Poisson-like. Clipped to [0.5, 1000]."""
    h = units[:, max(0, t - DISP):t]
    with np.errstate(invalid="ignore", divide="ignore"):
        m = np.nanmean(h, axis=1)
        v = np.nanvar(h, axis=1)
        r = np.where(v > m, m * m / (v - m), 1000.0)
    return np.clip(np.nan_to_num(r, nan=1000.0), 0.5, 1000.0)


def poisson_cdf(k: float, lam: float) -> float:
    if lam <= 0:
        return 1.0
    k = int(math.floor(k))
    term = math.exp(-lam)
    s = term
    for i in range(1, k + 1):
        term *= lam / i
        s += term
    return min(1.0, s)


def nb_cdf(k: float, mu: float, r: float) -> float:
    """P(X <= k) for a negative binomial with mean mu and size r."""
    if mu <= 0:
        return 1.0
    p = r / (r + mu)
    k = int(math.floor(k))
    term = p ** r
    s = term
    for i in range(1, k + 1):
        term *= (i - 1 + r) / i * (1 - p)
        s += term
    return min(1.0, s)


def scores(units_t: np.ndarray, mu: np.ndarray, r: np.ndarray, stocked: np.ndarray) -> dict[str, np.ndarray]:
    """Per series for one week: a surprise score per detector (higher = more anomalous); NaN where unscored.
    A week at or above its expected sales scores 0 (lost sales are about low weeks)."""
    n = len(units_t)
    ok = stocked & ~np.isnan(units_t) & ~np.isnan(mu) & (mu > 0)
    out = {k: np.where(ok, 0.0, np.nan) for k in ("zero_rule", "poisson", "neg_binomial")}
    out["zero_rule"] = np.where(ok & (units_t == 0), mu, out["zero_rule"])
    for i in np.where(ok & (units_t < mu))[0]:
        u = units_t[i]
        out["poisson"][i] = -math.log10(max(poisson_cdf(u, mu[i]), 1e-300))
        out["neg_binomial"][i] = -math.log10(max(nb_cdf(u, mu[i], r[i]), 1e-300))
    return out


def score_weeks(units: np.ndarray, stocked: np.ndarray, weeks: range) -> dict[str, np.ndarray]:
    """Scores for every series over `weeks` (series x len(weeks)), each week judged on history before it."""
    out = {k: np.full((units.shape[0], len(weeks)), np.nan) for k in ("zero_rule", "poisson", "neg_binomial")}
    for j, t in enumerate(weeks):
        s = scores(units[:, t], expected(units, t), dispersion(units, t), stocked[:, t])
        for k in out:
            out[k][:, j] = s[k]
    return out


def inject(units: np.ndarray, price: np.ndarray, weeks: range, rate: float, rng: np.random.Generator):
    """Plant stock-outs in stocked weeks of series that sell at least 1 a week on average: half full (0 units
    for 1 to 3 weeks), half partial (30% of sales). Returns the changed copy and a mask of planted weeks."""
    U = units.copy()
    planted = np.zeros(U.shape, dtype=bool)
    lo, hi = weeks.start, weeks.stop
    with np.errstate(invalid="ignore"):
        movers = np.nanmean(units[:, max(0, lo - 26):lo], axis=1) >= 1
    for i in np.where(movers)[0]:
        if rng.random() >= rate:
            continue
        t = int(rng.integers(lo, hi))
        if np.isnan(price[i, t]) or np.isnan(U[i, t]):
            continue
        full = rng.random() < 0.5
        span = int(rng.integers(1, 4)) if full else 1
        for k in range(t, min(t + span, hi)):
            if np.isnan(price[i, k]) or np.isnan(U[i, k]):
                break
            U[i, k] = 0.0 if full else np.floor(U[i, k] * 0.3)
            planted[i, k] = True
    return U, planted


def threshold_for(rate: float, clean_scores: np.ndarray) -> float:
    """The score above which a detector flags `rate` of ordinary weeks."""
    v = clean_scores[~np.isnan(clean_scores)]
    return float(np.quantile(v, 1 - rate)) if len(v) else float("inf")
