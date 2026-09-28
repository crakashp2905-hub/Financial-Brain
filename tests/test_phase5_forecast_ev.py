"""The forecast-to-expected-value bridge: the arrow that was missing from the loop.

Two claims carry this file.

A coarsening must not invent or lose probability. The test is an identity: the probability-weighted
sum of the scenario returns has to equal the distribution's own mean, exactly. Every version that
reads off p05/p50/p95 and attaches probabilities to them fails it, which is how you know those
versions were making numbers up.

A stop must be placed where few paths *touch* it, not where few paths *end* below it. On real
distributions the terminal 10% quantile is touched by something closer to 19% of paths, so a stop
placed there is hit nearly twice as often as its own quantile claims.
"""
from __future__ import annotations

import datetime as dt
import random

import pytest

from financial_brain.decisions import expected_value as ev
from financial_brain.decisions import forecast_ev as FE
from financial_brain.forecasting import null
from financial_brain.forecasting.distribution import ForecastDistribution, ForecastError

AS_OF = dt.date(2026, 1, 1)


def _prices(n=800, mu=0.0005, sigma=0.018, seed=7, start=100.0):
    rng = random.Random(seed)
    px = [start]
    for _ in range(n):
        px.append(px[-1] * (1 + rng.gauss(mu, sigma)))
    return px


def _fc(n_paths=1000, seed=7, horizon=20, mu=0.0005, sigma=0.018):
    return null.Climatology(seed=seed).forecast(
        instrument="L0", as_of=AS_OF, prices=_prices(mu=mu, sigma=sigma, seed=seed),
        horizon=horizon, n_paths=n_paths)


def _flat(terminals, anchor=100.0, horizon=1):
    """A distribution whose paths go straight to the given terminal prices."""
    return ForecastDistribution(instrument="L0", as_of=AS_OF, horizon=horizon,
                                anchor=anchor, paths=[[t] * horizon for t in terminals],
                                model="fixture")


# ---------------------------------------------------------------- the coarsening identity
def test_the_scenarios_reproduce_the_forecasts_own_mean_exactly():
    """The identity that proves nothing was invented. A coarsening that fails it has either made up
    probability or dropped some, and the EV that follows is not the forecast's EV."""
    for seed in (1, 2, 3, 11):
        fc = _fc(seed=seed)
        b = FE.bridge(fc)
        assert b.assessment.expected_value == pytest.approx(fc.expected_return(), abs=1e-12)
        assert b.mean_error == pytest.approx(0.0, abs=1e-12)


def test_every_path_lands_in_exactly_one_band_so_no_mass_is_lost():
    fc = _fc()
    scen = FE.scenarios_from(fc)
    assert sum(s["paths"] for s in scen) == fc.n_paths
    assert sum(s["probability"] for s in scen) == pytest.approx(1.0)


def test_the_scenario_return_is_the_conditional_mean_not_the_band_boundary():
    """p05 is the boundary of the worst 5%, not what happens inside it. Using the boundary
    understates the loss by exactly the amount that matters for sizing."""
    fc = _fc()
    scen = FE.scenarios_from(fc)
    worst = scen[0]
    from financial_brain.forecasting.distribution import quantile
    boundary = quantile(fc.terminal_returns(), worst["probability"])
    assert worst["return"] < boundary, (
        f"the mean inside the worst band ({worst['return']:+.2%}) must be below its boundary "
        f"({boundary:+.2%})")


def test_the_bands_are_ordered_worst_first_and_monotone():
    fc = _fc()
    scen = FE.scenarios_from(fc)
    rets = [s["return"] for s in scen]
    assert rets == sorted(rets)
    assert [s["name"] for s in scen] == list(FE.DEFAULT_NAMES)


def test_a_thin_sample_is_refused_rather_than_coarsened():
    """The outermost band of a 50-path sample holds five outcomes, and a conditional mean over five
    draws is a draw."""
    fc = _fc(n_paths=50)
    with pytest.raises(ForecastError, match="too few to coarsen"):
        FE.scenarios_from(fc)


def test_edges_that_are_not_increasing_probabilities_are_refused():
    fc = _fc()
    with pytest.raises(ForecastError, match="increasing"):
        FE.scenarios_from(fc, edges=(0.5, 0.2), names=("a", "b", "c"))
    with pytest.raises(ForecastError, match="increasing"):
        FE.scenarios_from(fc, edges=(0.0, 0.5), names=("a", "b", "c"))


def test_the_band_count_must_match_the_names_given():
    fc = _fc()
    with pytest.raises(ForecastError, match="bands"):
        FE.scenarios_from(fc, edges=(0.1, 0.9), names=("only", "two"))


def test_a_coarser_partition_still_satisfies_the_identity():
    """The identity is a property of the construction, not of the default edges."""
    fc = _fc()
    b = FE.bridge(fc, edges=(0.5,), names=("down", "up"))
    assert len(b.scenarios) == 2
    assert b.assessment.expected_value == pytest.approx(fc.expected_return(), abs=1e-12)


# ------------------------------------------------------------- the expected-value refusals
def test_a_forecast_where_nothing_loses_money_is_refused_by_the_ev_engine():
    """A distribution whose worst tenth is still positive has not named the way it is wrong, and it
    must fail here rather than be waved through because a model produced it."""
    fc = _flat([100.0 * (1 + 0.02 + 0.001 * i) for i in range(200)])
    with pytest.raises(ev.EVError, match="no scenario loses money"):
        FE.bridge(fc)


def test_a_negative_expected_value_produces_no_position_and_says_so():
    fc = _fc(mu=-0.002, seed=5)
    p = FE.proposal(fc, portfolio_inr=1_000_000.0)
    assert p["forecast"]["expected_value"] < 0
    assert p["proposed_weight"] == 0.0
    assert "not positive" in p["why_not"]
    assert "sizing" not in p


# ------------------------------------------------------------------- the invalidation level
def test_the_terminal_quantile_is_touched_far_more_often_than_its_own_quantile():
    """The reason first passage is the default. A stop placed at the terminal 10% quantile is hit
    close to twice as often as 10%, because a path can dip through it and recover."""
    fc = _fc()
    inv = FE.invalidation_from(fc, alpha=0.10)
    assert inv["terminal_quantile_touch_rate"] > 1.5 * inv["alpha"], (
        f"the terminal quantile was touched {inv['terminal_quantile_touch_rate']:.1%} of the time")
    assert inv["first_passage_touch_rate"] == pytest.approx(inv["alpha"], abs=0.03)
    assert inv["first_passage"] < inv["terminal_quantile"], (
        "the honest stop is the further-away one")
    assert inv["use"] == "first_passage"


def test_the_touch_rate_is_stated_as_a_floor_because_paths_are_closes():
    fc = _fc()
    inv = FE.invalidation_from(fc)
    assert "floor" in inv["why"]


def test_an_alpha_outside_the_usable_range_is_refused():
    fc = _fc()
    for bad in (0.0, 0.5, 0.9, -0.1):
        with pytest.raises(ForecastError, match="alpha"):
            FE.invalidation_from(fc, alpha=bad)


# ----------------------------------------------------------------------- the whole arrow
def test_a_forecast_becomes_a_sized_proposal_through_the_invalidation_not_the_confidence():
    """thesis -> invalidation -> size, with the distribution supplying the invalidation. The position
    is whatever loses the risk budget if the stop trades, so a wider stop must give a smaller one."""
    fc = _fc()
    tight = FE.proposal(fc, portfolio_inr=1_000_000.0, alpha=0.25)
    wide = FE.proposal(fc, portfolio_inr=1_000_000.0, alpha=0.02)
    assert tight["invalidation"]["first_passage"] > wide["invalidation"]["first_passage"]
    assert tight["proposed_weight"] > wide["proposed_weight"], (
        "a stop further from the entry has to buy less, not more")


def test_the_risk_budget_is_what_is_lost_when_the_invalidation_trades():
    fc = _fc()
    p = FE.proposal(fc, portfolio_inr=1_000_000.0, risk_budget=0.005)
    s = p["sizing"]
    loss = s["shares"] * (fc.anchor - p["invalidation"]["first_passage"])
    assert loss <= 1_000_000.0 * 0.005 + fc.anchor, "one share of rounding, no more"
    assert loss == pytest.approx(1_000_000.0 * 0.005, rel=0.02)


def test_the_proposal_is_a_weight_and_never_an_approval():
    """The portfolio gate turns a weight into an approved weight and can still refuse. A proposal
    that called itself a decision would route around every limit in risk/limits.py."""
    fc = _fc()
    p = FE.proposal(fc, portfolio_inr=1_000_000.0)
    assert "proposed_weight" in p
    assert "approved_weight" not in p and "verdict" not in p


def test_a_stop_that_sits_above_the_entry_proposes_nothing_rather_than_an_infinite_position():
    """``size`` divides by the distance from entry to invalidation, so a level at or above the entry
    is a division by zero or a negative position. It has to be refused with a reason.

    Reachable only at a wide alpha: 150 of 1,000 paths dip below the anchor and end there, which
    gives the worst band a loss so the EV engine is satisfied, while the 20% first-passage level sits
    among the 850 paths that never went below it at all.
    """
    horizon = 5
    paths = [[100.0 + 2.0 * k + 0.01 * i for k in range(1, horizon + 1)]
             for i in range(850)]
    paths += [[100.0 - 1.0 * k - 0.01 * i for k in range(1, horizon + 1)]
              for i in range(150)]
    fc = ForecastDistribution(instrument="L0", as_of=AS_OF, horizon=horizon, anchor=100.0,
                              paths=paths, model="fixture")

    inv = FE.invalidation_from(fc, alpha=0.20)
    assert inv["first_passage"] >= fc.anchor, (
        "with only 150 of 1,000 paths ever below the anchor, the 20% touch level is above it")

    p = FE.proposal(fc, portfolio_inr=1_000_000.0, alpha=0.20)
    assert p["proposed_weight"] == 0.0
    assert "no distance to be wrong over" in p["why_not"]
    assert "sizing" not in p

    # At a tighter alpha the level is genuinely below the entry and a position does get proposed.
    ok = FE.proposal(fc, portfolio_inr=1_000_000.0, alpha=0.10)
    assert ok["invalidation"]["first_passage"] < fc.anchor
    assert ok["proposed_weight"] > 0.0


def test_the_bridge_carries_the_model_and_horizon_so_a_proposal_is_traceable():
    fc = _fc(horizon=40)
    b = FE.bridge(fc)
    d = b.as_dict()
    assert d["model"] == "climatology" and d["horizon"] == 40
    assert d["n_paths"] == 1000
    assert d["anchor"] == pytest.approx(fc.anchor)
    assert "conditional means" in d["why"]
