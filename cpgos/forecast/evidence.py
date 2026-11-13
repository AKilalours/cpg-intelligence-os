"""Every real price cut in M5 with a controlled outcome: the evidence the promotion planner shows.

For each item-store week priced at or below 90% of its prior 8-week high: its sales relative to its own
prior 8 weeks, divided by the same ratio averaged over the same item at stores with no cut that week or the
8 weeks before (at least two of them). Holidays and seasons cancel; the cut remains. Vectorized by item.
"""

from __future__ import annotations

import warnings

import numpy as np

from cpgos.forecast.data import Weekly

DROP, BASE, MIN_CONTROLS = 0.9, 8, 2


def build(w: Weekly) -> dict[str, np.ndarray]:
    U, P = w.units, w.plan_price
    n, T = U.shape
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        reg = np.full((n, T), np.nan)
        base = np.full((n, T), np.nan)
        for t in range(BASE, T):
            reg[:, t] = np.nanmax(P[:, t - BASE:t], axis=1)
            base[:, t] = np.nanmean(U[:, t - BASE:t], axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            cut = P <= DROP * reg
            ratio = np.where(base > 0, U / base, np.nan)
    cut = np.nan_to_num(cut, nan=0).astype(bool)
    recent = np.zeros_like(cut)
    for k in range(1, BASE + 1):
        recent[:, k:] |= cut[:, :-k]
    control = ~cut & ~recent & np.isfinite(ratio)
    # Per item: mean control ratio and how many controls, each week.
    order = np.argsort(w.item, kind="stable")
    items, start = np.unique(w.item[order], return_index=True)
    r_sorted = np.where(control, ratio, 0.0)[order]
    c_sorted = control[order].astype(float)
    num = np.add.reduceat(r_sorted, start, axis=0)
    cnt = np.add.reduceat(c_sorted, start, axis=0)
    item_of = np.searchsorted(items, w.item)
    with np.errstate(invalid="ignore", divide="ignore"):
        ctrl_mean = np.where(cnt >= MIN_CONTROLS, num / cnt, np.nan)[item_of]
        lift = ratio / ctrl_mean - 1
    ii, tt = np.where(cut & np.isfinite(lift) & np.isfinite(ctrl_mean) & (ctrl_mean > 0))
    tt_ok = tt >= BASE
    ii, tt = ii[tt_ok], tt[tt_ok]
    depth = 1 - P[ii, tt] / reg[ii, tt]
    temporary = np.array([bool(np.any(P[i, t + 1:t + 5] >= 0.97 * reg[i, t])) for i, t in zip(ii, tt)])
    return {"series": ii, "week": tt, "depth": depth, "lift": lift[ii, tt], "temporary": temporary,
            "controls": cnt[item_of][ii, tt], "regular": reg[ii, tt], "units": U[ii, tt], "baseline": base[ii, tt]}


BANDS = ((0.10, 0.15), (0.15, 0.25), (0.25, 1.01))


def band(d: np.ndarray) -> np.ndarray:
    return np.select([(d >= lo) & (d < hi) for lo, hi in BANDS], list(range(len(BANDS))), -1)


def dept_band_median(ev: dict, dept_of_series: np.ndarray, mask: np.ndarray) -> dict[tuple[str, int], float]:
    """Median controlled lift by department and depth band, from the events in mask (earlier weeks only)."""
    out = {}
    dep, b, lift = dept_of_series[ev["series"]][mask], band(ev["depth"])[mask], ev["lift"][mask]
    for key in set(zip(dep.tolist(), b.tolist())):
        m = (dep == key[0]) & (b == key[1])
        if m.sum() >= 10:
            out[key] = float(np.median(lift[m]))
    return out
