"""Promotion lift: what a price cut does to sales, predicted by the forecaster and checked against reality.

Predicted lift. Forecast a week twice from the same origin: once with the planned (cut) price, once with
the price left at its regular level (the highest price in the 8 weeks before the origin). The ratio, minus
one, is the lift the model expects from the cut.

Actual lift (difference in differences). A store's sales in a promoted week, relative to its own normal
week, divided by the same ratio for the same item at stores that did not cut the price that week. Holidays,
paydays and seasons lift every store alike and cancel; what remains is the cut. Without the control, a
holiday week would be credited to the discount.
"""

from __future__ import annotations

import numpy as np

from cpgos.forecast.data import HORIZON, Weekly

DROP = 0.9          # a week priced at or below 90% of its prior 8-week high is a promotion
BASE_WEEKS = 8


def regular_price(w: Weekly, o: int) -> np.ndarray:
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmax(w.price[:, max(0, o - BASE_WEEKS):o], axis=1)


def events(w: Weekly, o: int) -> list[tuple[int, int]]:
    """(series, week) pairs in [o, o + HORIZON) planned at or below 90% of the regular price at the origin."""
    reg = regular_price(w, o)
    out = []
    for t in range(o, min(o + HORIZON, w.plan_price.shape[1])):
        with np.errstate(invalid="ignore"):
            hit = np.where(w.plan_price[:, t] <= DROP * reg)[0]
        out += [(int(i), t) for i in hit]
    return out


def actual_lift(units: np.ndarray, plan: np.ndarray, reg: np.ndarray, item: np.ndarray, i: int, t: int,
                min_controls: int = 2) -> float | None:
    """Difference in differences against the same item at stores not promoting in week t."""
    def ratio(j):
        base = np.nanmean(units[j, t - BASE_WEEKS:t])
        return units[j, t] / base if base and base > 0 and not np.isnan(units[j, t]) else np.nan
    same = np.where(item == item[i])[0]
    with np.errstate(invalid="ignore"):
        controls = [j for j in same if j != i and not (plan[j, t] <= DROP * reg[j])
                    and not np.any(plan[j, t - BASE_WEEKS:t] <= DROP * reg[j])]
    r_i = ratio(i)
    r_c = np.array([ratio(j) for j in controls])
    r_c = r_c[np.isfinite(r_c)]
    if np.isnan(r_i) or len(r_c) < min_controls or r_c.mean() <= 0:
        return None
    return float(r_i / r_c.mean() - 1)


def naive_lift(units: np.ndarray, i: int, t: int) -> float | None:
    """Promoted week against the store's own normal week, with no control: what a careless analysis reports."""
    base = np.nanmean(units[i, t - BASE_WEEKS:t])
    return float(units[i, t] / base - 1) if base and base > 0 and not np.isnan(units[i, t]) else None


def predicted_lifts(model, w: Weekly, o: int, evs: list[tuple[int, int]]) -> np.ndarray:
    """Model forecast with the planned (cut) price over the same forecast with the regular price, minus one,
    for each event. Only the plan columns differ between the two runs."""
    from cpgos.forecast import gbm
    reg = regular_price(w, o)
    X = gbm.features(w, o)
    cf = w.plan_price.copy()
    for i, t in evs:
        cf[i, t] = reg[i]
    w_cf = Weekly(w.units, w.price, w.weeks, w.item, w.store, w.dept, w.cat, cf, w.events, w.snap)
    Xc = gbm.features(w_cf, o)
    rows = np.array([i * HORIZON + (t - o) for i, t in evs], dtype=int)
    a, b = model.predict(X[rows]), model.predict(Xc[rows])
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(b > 0, a / b - 1, np.nan)
