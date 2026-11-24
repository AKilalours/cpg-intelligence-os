"""Lost-sales detection: the tail probabilities, and planted stock-outs found where noise is not."""

from __future__ import annotations

import math

import numpy as np
import pytest

from cpgos.forecast import anomaly as an


def test_poisson_and_negative_binomial_tails():
    assert an.poisson_cdf(0, 10) == pytest.approx(math.exp(-10))
    assert an.poisson_cdf(10, 10) == pytest.approx(0.58304, abs=1e-4)
    # A negative binomial with a huge size is a Poisson; a small size makes a zero far less surprising.
    assert an.nb_cdf(0, 10, 1e6) == pytest.approx(math.exp(-10), rel=1e-3)
    assert an.nb_cdf(0, 10, 1.0) == pytest.approx(1 / 11)


def test_zero_week_of_a_steady_seller_is_surprising_and_of_a_lumpy_one_is_not():
    units_t = np.array([0.0, 0.0])
    mu, r, stocked = np.array([12.0, 12.0]), np.array([1000.0, 0.6]), np.array([True, True])
    s = an.scores(units_t, mu, r, stocked)
    assert s["neg_binomial"][0] > 4 > s["neg_binomial"][1]          # steady: 1 in 10^5; lumpy: ordinary
    assert s["poisson"][0] == pytest.approx(s["poisson"][1])        # Poisson cannot tell them apart


def test_unstocked_weeks_are_never_scored():
    s = an.scores(np.array([0.0]), np.array([10.0]), np.array([1000.0]), np.array([False]))
    assert all(np.isnan(v[0]) for v in s.values())


def test_injection_only_touches_stocked_weeks_of_movers():
    rng = np.random.default_rng(0)
    U = np.full((50, 40), 5.0)
    U[:10] = 0.2                                                     # slow movers: never planted
    P = np.ones_like(U)
    P[20:30, 30:] = np.nan                                           # not stocked late: never planted
    U2, planted = an.inject(U, P, range(30, 40), 1.0, rng)
    assert not planted[:10].any() and not planted[20:30].any() and planted.any()
    assert np.all((U2[planted] == 0) | (U2[planted] == 1))           # zero, or 30% of 5 rounded down
    assert np.array_equal(U2[~planted], U[~planted])
