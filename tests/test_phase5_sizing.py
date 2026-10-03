"""Sizing under a shared regime risk.

The module exists because all four surviving signals reverse together in a crisis, so the usual sizing
arithmetic - which assumes an unconditional distribution - sizes against a distribution the portfolio
is never in. These tests pin the three claims that follow from that: a mixture is not its average, a
fat tail is not a normal one, and a result of zero means do not hold rather than hold nothing.
"""
from __future__ import annotations

import random
from statistics import NormalDist

import pytest

from financial_brain.risk import sizing as S

_N = NormalDist()


def _normal(n=4000, mu=0.0, sd=1.0, seed=1):
    rng = random.Random(seed)
    return [rng.gauss(mu, sd) for _ in range(n)]


def _skewed(n=4000, seed=2):
    """Left-skewed and fat-tailed, like an equity return series."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        x = rng.gauss(0, 1)
        out.append(x - 3.0 * abs(rng.gauss(0, 1)) if rng.random() < 0.05 else x)
    return out


# ------------------------------------------------------------------- Cornish-Fisher
def test_on_normal_data_the_correction_is_negligible():
    cf = S.cornish_fisher(_normal(), alpha=0.05)
    assert abs(cf["skew"]) < 0.15 and abs(cf["excess_kurtosis"]) < 0.3
    assert cf["cornish_fisher_quantile"] == pytest.approx(
        cf["gaussian_quantile"], abs=0.08)


def test_a_left_skewed_fat_tail_makes_the_gaussian_quantile_understate_the_loss():
    """The bias flagged against the Grinold estimates before they were computed: a normal quantile
    assumes zero skew and zero excess kurtosis, and equity returns have neither."""
    cf = S.cornish_fisher(_skewed(), alpha=0.05)
    assert cf["skew"] < -0.3, "the fixture should be left-skewed"
    assert cf["excess_kurtosis"] > 0.5, "and fat-tailed"
    assert cf["cornish_fisher_quantile"] < cf["gaussian_quantile"], (
        "the corrected quantile must be the more pessimistic one")
    assert cf["adjustment"] < 0


def test_an_inverted_expansion_is_reported_rather_than_returned_silently():
    """Past roughly |skew| > 2 the expansion stops being monotone and can move the wrong way. An
    inverted result looks like an ordinary number."""
    rng = random.Random(7)
    violent = [(-40.0 if rng.random() < 0.02 else rng.gauss(0, 1)) for _ in range(4000)]
    cf = S.cornish_fisher(violent, alpha=0.05)
    assert abs(cf["skew"]) > 2
    assert cf["valid"] is False
    assert "inverted" in cf["why"]


def test_too_few_observations_for_a_fourth_moment_is_refused():
    with pytest.raises(S.SizingError, match="too few"):
        S.cornish_fisher([0.1, -0.2, 0.3])


# --------------------------------------------------------------------------- EVT
def test_the_gpd_shape_recovers_a_planted_heavy_tail():
    """A Pareto(a) tail has true xi = 1/a; a Gaussian tail has xi near zero. Getting the PWM weight
    direction backwards reported 3.5 for Pareto(2) and 4.2 for the Gaussian - a heavier tail for the
    thin series than the heavy one, which is how the bug was caught."""
    rng = random.Random(3)
    heavy = [-(rng.paretovariate(2.0) - 1) for _ in range(6000)]
    thin = _normal(6000, seed=4)
    xi_heavy = S.pot_tail(heavy)["xi"]
    xi_thin = S.pot_tail(thin)["xi"]
    assert xi_heavy > xi_thin, f"heavy {xi_heavy:.2f} vs thin {xi_thin:.2f}"
    assert xi_heavy == pytest.approx(0.5, abs=0.25), (
        f"Pareto(2) has true xi = 0.5, got {xi_heavy:.2f}")
    assert abs(xi_thin) < 0.3, f"a Gaussian tail should have xi near zero, got {xi_thin:.2f}"


def test_an_infinite_variance_tail_is_flagged_because_it_breaks_every_variance_sizer():
    rng = random.Random(5)
    # xi around 1/1.2 > 0.5: the variance does not exist.
    nasty = [-(rng.paretovariate(1.2) - 1) for _ in range(4000)]
    r = S.pot_tail(nasty)
    assert r["xi"] > 0.5
    assert r["finite_variance"] is False
    assert "meaningless" in r["why"]


def test_too_few_exceedances_to_fit_two_parameters_is_refused():
    with pytest.raises(S.SizingError, match="too few"):
        S.pot_tail(_normal(100), tail=0.05)


# ----------------------------------------------------------------- mixture Kelly
def _crisis_states(p_crisis=0.03):
    """The measured regime picture: momentum earns +5.6% in benign states and -13.2% in a crisis."""
    return [S.State("benign", 1 - p_crisis, 0.056, 0.12),
            S.State("crisis", p_crisis, -0.132, 0.25)]


def test_the_mixture_sizes_smaller_than_kelly_on_the_pooled_moments():
    """The whole point. Pooled moments describe a distribution the portfolio is never in: a signal
    earning +5.6% benign and -13.2% in a crisis has an average that describes neither."""
    r = S.mixture_kelly(_crisis_states())
    assert r["naive_kelly_on_pooled_moments"] > r["full_kelly"], (
        f"pooled Kelly {r['naive_kelly_on_pooled_moments']:.2f} should exceed the mixture's "
        f"{r['full_kelly']:.2f}")
    assert r["overstatement"] > 0


def test_a_worse_crisis_shrinks_the_position_monotonically():
    sizes = [S.mixture_kelly(_crisis_states(p))["full_kelly"]
             for p in (0.01, 0.03, 0.08, 0.15)]
    assert sizes == sorted(sizes, reverse=True), f"position should shrink as crisis risk rises: {sizes}"


def test_a_bad_enough_crisis_says_do_not_hold_rather_than_hold_nothing():
    """A non-positive result is the arithmetic refusing the position, and is returned rather than
    clipped - a clipped zero and a genuinely negative edge look identical afterwards."""
    r = S.mixture_kelly([S.State("benign", 0.6, 0.02, 0.10),
                         S.State("crisis", 0.4, -0.20, 0.30)])
    assert r["full_kelly"] <= 0
    assert r["hold"] is False
    assert "do not hold" in r["why"]


def test_the_worst_state_is_named_so_a_size_can_be_explained():
    r = S.mixture_kelly(_crisis_states())
    assert r["worst_state"] == "crisis"
    assert r["worst_state_mean"] == pytest.approx(-0.132)
    assert r["worst_state_probability"] == pytest.approx(0.03)


def test_the_fraction_is_applied_on_top_of_full_kelly():
    full = S.mixture_kelly(_crisis_states(), fraction=1.0)
    half = S.mixture_kelly(_crisis_states(), fraction=0.5)
    assert half["sized"] == pytest.approx(full["full_kelly"] * 0.5)
    assert S.KELLY_FRACTION == 0.5


def test_probabilities_that_do_not_sum_to_one_are_refused():
    with pytest.raises(S.SizingError, match="sum to"):
        S.mixture_kelly([S.State("a", 0.6, 0.01, 0.1), S.State("b", 0.6, -0.01, 0.1)])


def test_a_single_benign_state_reproduces_ordinary_kelly_closely():
    """With one state and no crisis branch the mixture should not be doing anything exotic - once the
    cap is lifted far enough not to bind."""
    r = S.mixture_kelly([S.State("only", 1.0, 0.05, 0.20)], fraction=1.0, max_weight=4.0)
    assert r["at_max_weight"] is False
    assert r["full_kelly"] == pytest.approx(
        r["naive_kelly_on_pooled_moments"], rel=0.35)
    assert r["hold"] is True


def test_a_position_sitting_on_the_cap_says_so():
    """A bound that silently binds looks exactly like an optimum."""
    r = S.mixture_kelly([S.State("only", 1.0, 0.05, 0.20)], fraction=1.0, max_weight=0.25)
    assert r["at_max_weight"] is True
    assert r["full_kelly"] == pytest.approx(0.25, abs=1e-3)
    assert "cap chose the size" in r["why"]


# ---------------------------------------------------------------------------- CDaR
def test_cdar_is_milder_than_the_single_worst_drawdown():
    rng = random.Random(11)
    eq, v = [], 100.0
    for _ in range(2000):
        v *= 1 + rng.gauss(0.0003, 0.012)
        eq.append(v)
    r = S.cdar(eq)
    assert r["max_drawdown"] <= r["cdar"] <= 0
    assert r["episodes_averaged"] > 1
    assert "one observation" in r["why"]


def test_a_monotonically_rising_curve_has_no_drawdown():
    r = S.cdar([100.0 * (1.001 ** i) for i in range(500)])
    assert r["max_drawdown"] == pytest.approx(0.0)
    assert r["cdar"] == pytest.approx(0.0)


def test_a_curve_too_short_for_a_drawdown_distribution_is_refused():
    with pytest.raises(S.SizingError, match="too few"):
        S.cdar([100.0, 99.0, 101.0])
