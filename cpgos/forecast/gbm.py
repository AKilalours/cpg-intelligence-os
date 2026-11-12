"""A global gradient-boosted model: one LightGBM model across every series, Tweedie loss (many zero weeks).

features(...) is the only place features are made, for training and for forecasting alike: at origin o it
reads weeks before o and nothing else. Training rows come from earlier origins whose 8-week targets all end
before the forecast origin, so a model never learns from the weeks it is asked about. Not used in this
version: planned future prices (a brand knows its promotion calendar; that is the next step, stated).
"""

from __future__ import annotations

import numpy as np

from cpgos.forecast.data import HORIZON, Weekly

VERSION = "gbm-1.2.0"   # 1.1: planned price and calendar for the target week; 1.2: year-over-year growth
FEATURES = ("h", "week_of_year", "lag1", "lag2", "lag3", "lag4", "lag8", "mean4", "mean13", "mean26", "mean52",
            "sold_share13", "sold_share52", "weeks_since_sale", "last_year_same_week", "last_year_next8_mean",
            "price", "price_vs_8wk_high", "store", "dept", "cat",
            "plan_price", "plan_vs_8wk_high", "plan_vs_last", "sporting", "cultural", "national", "religious",
            "next_week_events", "snap_days", "yoy_growth13", "store_yoy_growth13")
CATEGORICAL = ("store", "dept", "cat")
PARAMS = {"objective": "tweedie", "tweedie_variance_power": 1.2, "learning_rate": 0.05, "num_leaves": 63,
          "min_data_in_leaf": 200, "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1,
          "lambda_l2": 1.0, "verbose": -1, "num_threads": 0, "seed": 7}
ROUNDS = 400


def _codes(a: np.ndarray) -> np.ndarray:
    _, inv = np.unique(a, return_inverse=True)
    return inv.astype(np.float32)


def features(w: Weekly, o: int) -> np.ndarray:
    """(series * HORIZON, len(FEATURES)) for forecast origin o, rows ordered series-major then h = 1..8."""
    U, P = w.units[:, :o], w.price[:, :o]
    n = U.shape[0]
    with np.errstate(invalid="ignore", divide="ignore"):
        lag = lambda k: U[:, o - k] if o - k >= 0 else np.full(n, np.nan)
        mean = lambda k: np.nanmean(U[:, max(0, o - k):o], axis=1) if o > 0 else np.full(n, np.nan)
        share = lambda k: np.nanmean((U[:, max(0, o - k):o] > 0).astype(float) + np.where(np.isnan(U[:, max(0, o - k):o]), np.nan, 0), axis=1)
        sold = np.where(np.nan_to_num(U) > 0, np.arange(o)[None, :], -1).max(axis=1) if o > 0 else np.full(n, -1)
        since = np.where(sold >= 0, o - sold, np.nan).astype(float)
        last_price = np.full(n, np.nan)
        for t in range(max(0, o - 8), o):
            last_price = np.where(np.isnan(P[:, t]), last_price, P[:, t])
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            high8 = np.nanmax(P[:, max(0, o - 8):o], axis=1) if o > 0 else np.full(n, np.nan)
        ly_next8 = np.nanmean(U[:, max(0, o - 52):max(0, o - 44)], axis=1) if o >= 52 else np.full(n, np.nan)
        prev13 = np.nanmean(U[:, o - 65:o - 52], axis=1) if o >= 65 else np.full(n, np.nan)
        yoy = mean(13) / prev13
        # The same ratio for the whole store: steadier than one item's, and it carries the trend the
        # item-level ratio is too noisy to show.
        _, sid = np.unique(w.store, return_inverse=True)
        tot_now = np.bincount(sid, np.nan_to_num(mean(13)))
        tot_prev = np.bincount(sid, np.nan_to_num(prev13))
        store_yoy = (tot_now / np.where(tot_prev > 0, tot_prev, np.nan))[sid]
    base = np.column_stack([lag(1), lag(2), lag(3), lag(4), lag(8), mean(4), mean(13), mean(26), mean(52),
                            share(13), share(52), since]).astype(np.float32)
    growth = np.column_stack([yoy, store_yoy]).astype(np.float32)
    cats = np.column_stack([_codes(w.store), _codes(w.dept), _codes(w.cat)]).astype(np.float32)
    rows = []
    for h in range(1, HORIZON + 1):
        t = o + h - 1
        woy = np.full(n, (w.weeks[0].isocalendar()[1] + t - 1) % 52 + 1, dtype=np.float32) if w.weeks else np.zeros(n)
        ly = U[:, t - 52] if 0 <= t - 52 < o else np.full(n, np.nan)
        with np.errstate(invalid="ignore", divide="ignore"):
            pv = last_price / high8
        known = _known(w, t, last_price, high8, n)
        rows.append(np.column_stack([np.full(n, h, np.float32), woy, base, ly, ly_next8, last_price, pv, cats, known, growth]))
    X = np.stack(rows, axis=1).reshape(n * HORIZON, len(FEATURES)).astype(np.float32)
    return X


def _known(w: Weekly, t: int, last_price, high8, n) -> np.ndarray:
    """What is known before week t happens: the planned shelf price and the calendar. Never sales."""
    nan = np.full(n, np.nan)
    pp = w.plan_price[:, t] if w.plan_price is not None and t < w.plan_price.shape[1] else nan
    with np.errstate(invalid="ignore", divide="ignore"):
        cols = [pp, pp / high8, pp / last_price]
    if w.events is not None and t < len(w.events):
        ev = [np.full(n, w.events[t, k]) for k in range(4)]
        nxt = np.full(n, w.events[t + 1].sum() if t + 1 < len(w.events) else np.nan)
        sn = w.snap[:, t]
    else:
        ev, nxt, sn = [nan] * 4, nan, nan
    return np.column_stack(cols + ev + [nxt, sn])


def targets(w: Weekly, o: int) -> np.ndarray:
    return w.units[:, o:o + HORIZON].reshape(-1).astype(np.float32)


def train(w: Weekly, origin: int, history_weeks: int = 104, every: int = 4, sample: float = 0.25, seed: int = 7):
    """Fit on origins whose targets end before `origin`. w must not contain weeks at or after `origin`
    beyond what the caller allows; the caller passes w.upto(origin) for a forecast at `origin`."""
    import lightgbm as lgb
    rng = np.random.default_rng(seed)
    last = origin - HORIZON
    origins = list(range(max(60, last - history_weeks), last + 1, every))
    Xs, ys = [], []
    for o in origins:
        X, y = features(w, o), targets(w, o)
        keep = ~np.isnan(y) & (rng.random(len(y)) < sample)
        Xs.append(X[keep])
        ys.append(y[keep])
    X, y = np.concatenate(Xs), np.concatenate(ys)
    ds = lgb.Dataset(X, y, feature_name=list(FEATURES), categorical_feature=list(CATEGORICAL), free_raw_data=True)
    return lgb.train(PARAMS, ds, num_boost_round=ROUNDS)


def forecast(model, w: Weekly, o: int) -> np.ndarray:
    return np.maximum(model.predict(features(w, o)), 0).reshape(-1, HORIZON)


def lgbm(w_hist: Weekly, o: int) -> np.ndarray:
    """Train on history before o, forecast o .. o+7. w_hist holds only weeks before o."""
    assert w_hist.units.shape[1] == o, "the model must not be given weeks at or after the forecast origin"
    return forecast(train(w_hist, o), w_hist, o)
