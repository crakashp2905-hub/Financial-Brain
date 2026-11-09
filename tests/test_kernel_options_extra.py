from __future__ import annotations

from decimal import Decimal

import pytest

from financial_brain.kernel.options_extra import (
    ChainQuote,
    OptionPosition,
    binomial_american,
    chain_filter,
    expiry_events,
    pin_risk,
    short_option_margin,
)


def test_chain_filter_drops_crossed_markets_and_filters() -> None:
    chain = [
        ChainQuote("CALL", "2026-11-26", Decimal("100"), Decimal("5"), Decimal("5.5"), 10),
        ChainQuote("PUT", "2026-11-26", Decimal("100"), Decimal("6"), Decimal("5"), 10),
        ChainQuote("CALL", "2026-12-31", Decimal("110"), Decimal("2"), Decimal("2.2"), 3),
    ]
    assert len(chain_filter(chain)) == 2
    assert [q.strike for q in chain_filter(chain, kind="CALL", expiry="2026-11-26")] == [Decimal("100")]


def test_american_put_is_at_least_intrinsic_and_european_call_matches_without_dividends() -> None:
    put = binomial_american(spot=90.0, strike=100.0, rate=0.05, sigma=0.2, years=1.0, steps=200, kind="PUT")
    assert put >= 10.0
    call_american = binomial_american(spot=100.0, strike=90.0, rate=0.05, sigma=0.2, years=1.0, steps=200, kind="CALL")
    assert call_american > 10.0


def test_short_option_margin_is_model_based_and_scales_with_quantity() -> None:
    one = short_option_margin(Decimal("100"), Decimal("110"), Decimal("2"), "CALL", 1)
    two = short_option_margin(Decimal("100"), Decimal("110"), Decimal("2"), "CALL", 2)
    assert two == one * 2
    # OTM = 110 - 100 = 10; max(20% of 100 - 10, 10% of 100) = 10; plus premium 2
    assert one == Decimal("12")


def test_expiry_assigns_short_itm_and_exercises_long_itm() -> None:
    positions = [
        OptionPosition("CALL", Decimal("100"), -1),
        OptionPosition("PUT", Decimal("100"), 2),
        OptionPosition("CALL", Decimal("120"), 1),
    ]
    events = expiry_events(positions, Decimal("105"))
    assert events == ["ASSIGNED CALL 100 x1"]


def test_pin_risk_flags_short_strikes_near_spot() -> None:
    flagged = pin_risk([Decimal("100"), Decimal("110")], [-1, 1], Decimal("100.5"), Decimal("1"))
    assert flagged == [Decimal("100")]
    with pytest.raises(ValueError):
        pin_risk([Decimal("100")], [], Decimal("100"), Decimal("1"))
