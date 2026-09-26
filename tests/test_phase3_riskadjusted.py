"""Jensen's alpha: pin the estimator against planted answers before trusting it on data.

The first version of `riskadjusted` demeaned x before fitting, which makes the intercept equal
`mean(y)` - the book's own return over the risk-free rate, carrying no beta adjustment at all.
It reported momentum at +21% a year of alpha and low volatility clearing the significance bar,
and both were a rising market being read as skill.

Every test here would have failed that version. They are the check that should have come
before the measurement rather than after it.
"""
from __future__ import annotations

import random

import pytest

from financial_brain.evaluation.riskadjusted import RF_ANNUAL, SESSIONS, capm


def _rf(h):
    return (1 + RF_ANNUAL) ** (h / SESSIONS) - 1


def _planted(beta, alpha, n=160, h=20, noise=0.01, drift=0.02, seed=7):
    """A book built to be exactly `beta` times its benchmark plus `alpha`."""
    rng = random.Random(seed)
    bench = [drift + rng.gauss(0, 0.06) for _ in range(n)]
    rf = _rf(h)
    q = [rf + alpha + beta * (b - rf) + rng.gauss(0, noise) for b in bench]
    return q, bench


def test_a_book_identical_to_its_benchmark_has_no_alpha():
    """The one case with an exact answer: beta is one and alpha is zero, to machine
    precision. A demeaned fit returns the benchmark's own mean return here instead."""
    _, bench = _planted(1.0, 0.0)
    r = capm(bench, bench, horizon=20)
    assert r["beta"] == pytest.approx(1.0, abs=1e-9)
    assert r["alpha"] == pytest.approx(0.0, abs=1e-12)


def test_a_planted_beta_and_alpha_come_back():
    q, bench = _planted(0.6, 0.01)
    r = capm(q, bench, horizon=20)
    assert r["beta"] == pytest.approx(0.6, abs=0.03)
    assert r["alpha"] == pytest.approx(0.01, abs=0.003)
    assert r["alpha_t"] > 3


def test_a_low_beta_book_with_no_skill_shows_no_alpha():
    """The bug's signature. A book that is 0.6x its benchmark and nothing else has zero
    alpha - but in a rising market its own mean return is large and positive, so an
    estimator that reports mean(y) calls it skill."""
    q, bench = _planted(0.6, 0.0, drift=0.03)
    r = capm(q, bench, horizon=20)
    assert r["beta"] == pytest.approx(0.6, abs=0.03)
    assert abs(r["alpha"]) < 0.004
    assert abs(r["alpha_t"]) < 2.5
    # And the thing that would have caught it in the reported table: the raw excess of a
    # 0.6-beta book in a rising market is strongly negative while alpha is zero.
    assert r["raw_excess"] < -0.005


def test_beta_is_tested_against_one_not_zero():
    """"Is this book carrying less market than its benchmark" is the question a low-beta
    claim makes. Beta differing from zero is not news about anything."""
    q, bench = _planted(0.6, 0.0)
    r = capm(q, bench, horizon=20)
    assert r["beta_t_vs_one"] < -5
    q1, bench1 = _planted(1.0, 0.0)
    assert abs(capm(q1, bench1, horizon=20)["beta_t_vs_one"]) < 2


def test_serial_correlation_widens_the_standard_error():
    """Newey-West exists for this. Residuals that persist make an OLS t-statistic too large,
    and a HAC estimator must not agree with the naive one when they do."""
    rng = random.Random(11)
    n, h = 200, 20
    bench = [0.02 + rng.gauss(0, 0.06) for _ in range(n)]
    rf = _rf(h)
    # Highly persistent residual: an AR(1) with phi 0.8.
    e, u = 0.0, []
    for _ in range(n):
        e = 0.8 * e + rng.gauss(0, 0.01)
        u.append(e)
    q = [rf + 0.6 * (b - rf) + x for b, x in zip(bench, u)]
    wide = capm(q, bench, horizon=h, lag=20)
    tight = capm(q, bench, horizon=h, lag=0)
    assert abs(wide["alpha_t"]) < abs(tight["alpha_t"])


def test_too_few_rebalances_returns_nan_rather_than_a_number():
    """Five points is not a regression, and a plausible number from five points is worse
    than no number."""
    r = capm([0.01] * 5, [0.01] * 5, horizon=20)
    assert r["alpha"] != r["alpha"]        # NaN


def test_mismatched_lengths_are_refused():
    with pytest.raises(ValueError):
        capm([0.01] * 10, [0.01] * 9, horizon=20)


def test_the_risk_free_rate_is_scaled_to_the_horizon():
    """A 20-session hold does not earn a year of the risk-free rate."""
    assert capm([0.0] * 10, [0.0] * 10, horizon=20)["rf"] == pytest.approx(_rf(20))
    assert capm([0.0] * 10, [0.0] * 10, horizon=250)["rf"] == pytest.approx(RF_ANNUAL,
                                                                           abs=1e-9)
