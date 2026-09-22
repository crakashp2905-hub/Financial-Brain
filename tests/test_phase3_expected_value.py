"""Expected value and risk-based sizing (Phase 3 decision engine).

"The model is 82% confident" says nothing about how much is made when right, lost when
wrong, or how much to put on. These tests pin the arithmetic that replaces it - and the
sizing rule that runs thesis -> invalidation -> size rather than confidence -> size.
"""
from __future__ import annotations

import pytest

from financial_brain.decisions import expected_value as ev

BULL_BASE_BEAR = {"bull": {"probability": 0.35, "return": 0.25},
                  "base": {"probability": 0.45, "return": 0.10},
                  "bear": {"probability": 0.20, "return": -0.18}}


def test_expected_value_is_the_probability_weighted_return():
    a = ev.assess(BULL_BASE_BEAR)
    # 8.75% + 4.50% - 3.60% = 9.65%. Worth spelling out: the worked example this case
    # was taken from reported 8.15%, which is the kind of slip that makes a losing trade
    # look acceptable, and exactly why the sum is computed rather than quoted.
    assert a.expected_value == pytest.approx(0.35 * 0.25 + 0.45 * 0.10 + 0.20 * -0.18)
    assert a.expected_value == pytest.approx(0.0965)
    assert a.upside == pytest.approx(0.1325) and a.downside == pytest.approx(-0.036)
    assert a.worst == pytest.approx(-0.18)
    assert a.positive()


def test_probabilities_must_sum_to_one():
    with pytest.raises(ev.EVError, match="sum to"):
        ev.assess({"bull": {"probability": 0.6, "return": 0.2},
                   "bear": {"probability": 0.6, "return": -0.1}})


def test_a_thesis_must_name_the_way_it_loses():
    """Every scenario positive is not a distribution, it is a hope."""
    with pytest.raises(ev.EVError, match="no scenario loses money"):
        ev.assess({"bull": {"probability": 0.5, "return": 0.3},
                   "base": {"probability": 0.5, "return": 0.05}})


def test_one_scenario_is_a_forecast_not_a_distribution():
    with pytest.raises(ev.EVError, match="at least 2 scenarios"):
        ev.assess({"base": {"probability": 1.0, "return": 0.1}})


def test_size_comes_from_the_distance_to_being_wrong():
    """Rs 10 lakh, 0.5% at risk, entry 1000, invalidation 900: Rs 5,000 / Rs 100 = 50."""
    got = ev.size(portfolio_inr=1_000_000, entry=1000.0, invalidation=900.0,
                  risk_budget=0.005)
    assert got["shares"] == 50
    assert got["notional_inr"] == pytest.approx(50_000)
    assert got["rupees_at_risk"] == pytest.approx(5_000)
    assert got["stop_distance"] == pytest.approx(0.10)


def test_a_tighter_invalidation_earns_a_bigger_position():
    """The inversion that matters: conviction does not size a trade, distance does."""
    tight = ev.size(portfolio_inr=1_000_000, entry=1000.0, invalidation=940.0)
    wide = ev.size(portfolio_inr=1_000_000, entry=1000.0, invalidation=800.0)
    assert tight["shares"] > wide["shares"], "less distance to be wrong over, more shares"
    assert not tight["capped_by_max_weight"]
    # Both risk the same rupees; only the share count differs. That is the whole idea.
    assert tight["rupees_at_risk"] == pytest.approx(wide["rupees_at_risk"], rel=0.05)


def test_no_position_exceeds_the_weight_cap():
    got = ev.size(portfolio_inr=1_000_000, entry=1000.0, invalidation=999.0,
                  risk_budget=0.005, max_weight=0.10)
    assert got["capped_by_max_weight"] and got["weight"] <= 0.10


def test_an_invalidation_above_the_entry_is_refused():
    with pytest.raises(ev.EVError, match="must be below the entry"):
        ev.size(portfolio_inr=1_000_000, entry=100.0, invalidation=120.0)


def test_review_returns_both_the_arithmetic_and_the_size():
    out = ev.review({"scenarios": BULL_BASE_BEAR,
                     "sizing": {"entry": 1000.0, "invalidation": 900.0}},
                    portfolio_inr=1_000_000)
    assert out["expected_value"] == pytest.approx(0.0965)
    assert out["size"]["shares"] == 50
    assert "EV +9.7%" in out["describe"]
