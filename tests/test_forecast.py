"""Forecasting: methods behave as their definitions say, metrics match hand calculations, and the design
code cannot see the test weeks."""

from __future__ import annotations

import numpy as np
import pytest

from cpgos.forecast import metrics, models


def test_croston_on_a_regular_intermittent_series():
    H = np.array([[0, 0, 4, 0, 0, 4, 0, 0, 4.0]])
    f = models.croston(H, 3, alpha=0.1)
    assert f.shape == (1, 3) and f[0, 0] == pytest.approx(4 / 3, rel=1e-6)       # size 4 every 3 weeks
    assert models.sba(H, 1, alpha=0.1)[0, 0] == pytest.approx(4 / 3 * 0.95)


def test_tsb_forecast_decays_when_an_item_stops_selling_and_croston_does_not():
    H = np.array([[5, 5, 5, 5, 0, 0, 0, 0, 0, 0, 0, 0.0]])
    assert models.tsb(H, 1)[0, 0] < 5 * 0.5
    assert models.croston(H, 1)[0, 0] == pytest.approx(5.0)


def test_not_stocked_weeks_are_skipped_not_read_as_zero():
    H = np.array([[3, 3, np.nan, np.nan, 3, 3.0]])
    assert models.naive(H, 1)[0, 0] == 3
    assert models.ses(H, 1)[0, 0] == pytest.approx(3.0)
    assert models.tsb(H, 1)[0, 0] == pytest.approx(3.0)


def test_seasonal_naive_uses_the_same_week_last_year():
    H = np.arange(104, dtype=float)[None, :]
    assert models.seasonal_naive(H, 2)[0].tolist() == [52.0, 53.0]


def test_rmsse_matches_a_hand_calculation():
    train = np.array([[1, 3, 2, 4.0]])                     # one-step changes 2, -1, 2 -> mean square 3
    actual, fc = np.array([[2, 4.0]]), np.array([[3, 3.0]])  # errors 1, 1 -> mse 1
    assert metrics.rmsse(actual, fc, metrics.scale(train))[0] == pytest.approx((1 / 3) ** 0.5)


def test_demand_classes():
    smooth = [10, 11, 9, 10] * 10
    intermittent = [0, 0, 5, 0, 0, 5] * 7
    lumpy = [0, 0, 1, 0, 0, 30] * 7
    cls = metrics.demand_class(np.array([smooth[:40], intermittent[:40], lumpy[:40]], dtype=float))
    assert cls.tolist() == ["smooth", "intermittent", "lumpy"]


def _toy_weekly(n=40, T=130, seed=0):
    from datetime import date, timedelta
    from cpgos.forecast.data import Weekly
    rng = np.random.default_rng(seed)
    lam = rng.uniform(0.2, 6, n)[:, None] * (1 + 0.3 * np.sin(np.arange(T) / 52 * 2 * np.pi))[None, :]
    U = rng.poisson(lam).astype(float)
    U[:3, :20] = np.nan                                          # launched later
    P = np.full((n, T), 3.0)
    return Weekly(U, P, [date(2013, 1, 7) + timedelta(weeks=k) for k in range(T)],
                  np.array([f"I{i}" for i in range(n)]), np.array([f"S{i % 3}" for i in range(n)]),
                  np.array([f"D{i % 4}" for i in range(n)]), np.array(["FOODS"] * n))


def test_features_at_an_origin_never_read_later_weeks():
    from cpgos.forecast import gbm
    w = _toy_weekly()
    o = 100
    a = gbm.features(w, o)
    w2 = _toy_weekly()
    w2.units[:, o:] = 1e6                                        # poison every week from the origin on
    w2.price[:, o:] = 1e6
    assert np.array_equal(np.nan_to_num(a, nan=-7), np.nan_to_num(gbm.features(w2, o), nan=-7))


def test_training_and_serving_use_identical_features():
    # Train/serve skew: the rows the model learns from at origin o are the rows it is given to forecast
    # from at o, computed by one function from the same truncated history.
    from cpgos.forecast import gbm
    w = _toy_weekly()
    assert np.array_equal(np.nan_to_num(gbm.features(w.upto(100), 100), nan=-7),
                          np.nan_to_num(gbm.features(w, 100), nan=-7))


def test_lgbm_refuses_a_history_that_reaches_the_origin():
    from cpgos.forecast import gbm
    w = _toy_weekly()
    with pytest.raises(AssertionError, match="must not be given"):
        gbm.lgbm(w, 100)


def test_lgbm_learns_something_on_a_toy_panel():
    from cpgos.forecast import gbm
    w = _toy_weekly(n=120)
    o = 120
    fc = gbm.lgbm(w.upto(o), o)
    actual = w.units[:, o:o + 8]
    flat = models.ma13(w.units[:, :o], 8)
    sc = metrics.scale(w.units[:, :o])
    assert np.nanmean(metrics.rmsse(actual, fc, sc)) < 1.1 * np.nanmean(metrics.rmsse(actual, flat, sc))


def test_paired_bootstrap_sees_a_real_difference_and_not_a_fake_one():
    from cpgos.forecast import compare
    rng = np.random.default_rng(1)
    base = rng.uniform(0.5, 1.5, 3000)
    w = np.ones(3000)
    real = compare.paired_bootstrap(base * 0.95, base, w, n=500)
    assert real["ci95"][1] < 0
    noise = compare.paired_bootstrap(base + rng.normal(0, 0.05, 3000), base, w, n=500)
    assert noise["ci95"][0] < 0 < noise["ci95"][1]


def test_planned_price_and_calendar_may_look_ahead_but_sales_never_do():
    from cpgos.forecast import gbm
    w = _toy_weekly()
    w.plan_price, w.events = w.price.copy(), np.zeros((w.units.shape[1], 4))
    w.snap = np.zeros_like(w.units)
    o = 100
    a = gbm.features(w.upto(o), o)
    w.units[:, o:] = 1e6                                    # future sales: must change nothing
    assert np.array_equal(np.nan_to_num(a, nan=-7), np.nan_to_num(gbm.features(w.upto(o), o), nan=-7))
    w.plan_price[:, o:] = 1.0                               # a planned price cut: may change plan columns only
    b = gbm.features(w.upto(o), o)
    changed = {gbm.FEATURES[j] for j in np.where(~np.all(np.nan_to_num(a, nan=-7) == np.nan_to_num(b, nan=-7), axis=0))[0]}
    assert changed and changed <= {"plan_price", "plan_vs_8wk_high", "plan_vs_last"}
