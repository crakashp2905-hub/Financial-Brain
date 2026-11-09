from __future__ import annotations

from decimal import Decimal

import pytest

from financial_brain.kernel.batch20h_equity import (
    annualised_turnover,
    breadth,
    contribution,
    factor_decay_exit,
    gap_and_go,
    margin_balances,
    phase_returns,
    relative_strength,
    valuation_scenarios,
)
from financial_brain.kernel.batch20h_fx import (
    CurrencyPair,
    candle_from_ticks,
    carry_signal,
    convert_pnl,
    events_between,
    hedge_units,
    notional,
    pipette,
    pips,
    rollover_window,
    stress_fill,
    weekend_gap,
)


def test_currency_pair_validates_codes_and_pip_convention() -> None:
    assert CurrencyPair("EUR", "USD").pip_size == Decimal("0.0001")
    assert CurrencyPair("USD", "JPY").pip_size == Decimal("0.01")
    assert pipette(CurrencyPair("EUR", "USD")) == Decimal("0.00001")
    with pytest.raises(ValueError):
        CurrencyPair("eur", "USD")
    with pytest.raises(ValueError):
        CurrencyPair("EUR", "EUR")


def test_notional_and_pnl_conversion_to_account_currency() -> None:
    assert notional(100_000, Decimal("1.1")) == Decimal("110000.0")
    assert convert_pnl(Decimal("10"), Decimal("1.2")) == Decimal("12")


def test_stress_fill_is_adverse_to_the_side() -> None:
    assert stress_fill("BUY", Decimal("1.1"), Decimal("0.0001"), Decimal("3")) == Decimal("1.1003")
    assert stress_fill("SELL", Decimal("1.1"), Decimal("0.0001"), Decimal("3")) == Decimal("1.0997")


def test_rollover_window_and_weekend_gap() -> None:
    assert rollover_window(21) is True
    assert rollover_window(20) is False
    pct, flag = weekend_gap(Decimal("100"), Decimal("101.5"))
    assert pct == Decimal("0.015") and flag is True
    assert weekend_gap(Decimal("100"), Decimal("100.5"))[1] is False


def test_carry_sign_follows_net_of_swap_cost() -> None:
    assert carry_signal(Decimal("0.05"), Decimal("0.02"), Decimal("0.01")) == {"net_carry": Decimal("0.02"), "sign": 1}
    assert carry_signal(Decimal("0.05"), Decimal("0.02"), Decimal("0.04"))["sign"] == -1


def test_economic_calendar_fixtures_filter_by_time() -> None:
    assert events_between([(1, "CPI"), (5, "NFP")], 2, 6) == [(5, "NFP")]


def test_pips_are_signed_by_side_and_hedges_round_down() -> None:
    assert pips(Decimal("1.1000"), Decimal("1.1020"), Decimal("0.0001"), "BUY") == Decimal("20")
    assert pips(Decimal("1.1000"), Decimal("1.1020"), Decimal("0.0001"), "SELL") == Decimal("-20")
    assert hedge_units(1000, Decimal("0.5")) == 500
    assert hedge_units(1000, Decimal("0.3333")) == 333


def test_synthetic_candle_agrees_with_tick_extremes() -> None:
    ticks = [Decimal("1.0"), Decimal("1.2"), Decimal("0.9"), Decimal("1.1")]
    o, h, lo, c = candle_from_ticks(ticks)
    assert (o, h, lo, c) == (Decimal("1.0"), max(ticks), min(ticks), Decimal("1.1"))
    with pytest.raises(ValueError):
        candle_from_ticks([])


def test_margin_balance_states() -> None:
    assert margin_balances(Decimal("130"), Decimal("120"), Decimal("80")) == "OK"
    assert margin_balances(Decimal("100"), Decimal("120"), Decimal("80")) == "BELOW_INITIAL"
    assert margin_balances(Decimal("70"), Decimal("120"), Decimal("80")) == "MARGIN_CALL"


def test_breadth_net_and_share() -> None:
    out = breadth(60, 40)
    assert out["net"] == Decimal("20")
    assert out["advancing_share"] == Decimal("0.6")


def test_relative_strength_of_sector_against_index() -> None:
    assert float(relative_strength(Decimal("0.10"), Decimal("0.05"))) == pytest.approx(0.047619, rel=1e-4)


def test_annualised_turnover_scales_to_a_year() -> None:
    assert annualised_turnover([Decimal("1000"), Decimal("500")], Decimal("10000"), 252, 63) == Decimal("0.6")


def test_valuation_scenarios_are_earnings_times_multiple() -> None:
    assert valuation_scenarios(Decimal("5"), {"bear": Decimal("10"), "base": Decimal("15")}) == {
        "bear": Decimal("50"),
        "base": Decimal("75"),
    }


def test_factor_decay_exit_needs_consecutive_weak_periods() -> None:
    assert factor_decay_exit([1.0, 0.5, 0.2, 0.1], 0.3, consecutive=2) is True
    assert factor_decay_exit([1.0, 0.2, 0.5, 0.1], 0.3, consecutive=2) is False


def test_contributions_sum_to_portfolio_return() -> None:
    out = contribution({"A": Decimal("0.6"), "B": Decimal("0.4")}, {"A": Decimal("0.1"), "B": Decimal("-0.05")})
    assert out == {"A": Decimal("0.06"), "B": Decimal("-0.02")}
    assert sum(out.values()) == Decimal("0.04")
    with pytest.raises(ValueError):
        contribution({"A": Decimal("1")}, {"B": Decimal("0.1")})


def test_gap_and_go_needs_gap_above_and_a_bullish_bar() -> None:
    assert gap_and_go(Decimal("100"), Decimal("101"), Decimal("102")) is True
    assert gap_and_go(Decimal("100"), Decimal("101"), Decimal("100.5")) is False


def test_phase_returns_split_the_session() -> None:
    out = phase_returns(Decimal("100"), Decimal("102"), Decimal("101"))
    assert out["morning"] == Decimal("0.02")
    assert float(out["afternoon"]) == pytest.approx(-0.0098039, rel=1e-4)
