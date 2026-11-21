from __future__ import annotations

from decimal import Decimal

import pytest

from financial_brain.kernel.fills import (
    FillError,
    OhlcBar,
    charge_costs,
    fill_bar,
    half_spread_fill,
    next_bar_fill_index,
    resolve_stop_target,
    square_root_impact,
)

BAR = OhlcBar(open=100.0, high=104.0, low=98.0, close=102.0, volume=10_000)


def test_signal_at_close_fills_no_earlier_than_next_open() -> None:
    assert next_bar_fill_index(5, 10) == 6
    assert next_bar_fill_index(9, 10) is None


def test_stop_and_target_in_same_bar_resolve_adversely() -> None:
    outcome, price = resolve_stop_target(BAR, "BUY", stop=98.5, target=103.0)
    assert outcome == "STOP_AMBIGUOUS_ADVERSE" and price == 98.5


def test_clean_target_hit() -> None:
    assert resolve_stop_target(BAR, "BUY", stop=97.0, target=103.0) == ("TARGET", 103.0)


def test_gap_through_stop_fills_at_open_not_at_stop() -> None:
    gap = OhlcBar(open=95.0, high=96.0, low=94.0, close=95.5, volume=5_000)
    outcome, price = resolve_stop_target(gap, "BUY", stop=98.0, target=110.0)
    assert outcome == "STOP_GAP" and price == 95.0


def test_short_side_mirror_logic() -> None:
    gap_up = OhlcBar(open=110.0, high=111.0, low=109.0, close=110.5, volume=5_000)
    assert resolve_stop_target(gap_up, "SELL", stop=105.0, target=90.0) == ("STOP_GAP", 110.0)


def test_buy_fills_above_mid_and_sell_below() -> None:
    assert half_spread_fill(100.0, "BUY", 0.2) == pytest.approx(100.1)
    assert half_spread_fill(100.0, "SELL", 0.2) == pytest.approx(99.9)


def test_impact_is_monotonic_in_size_and_rejects_unknown_liquidity() -> None:
    small = square_root_impact(1_000, 1_000_000)
    large = square_root_impact(100_000, 1_000_000)
    assert 0 <= small < large
    with pytest.raises(FillError, match="unknown liquidity"):
        square_root_impact(1, 0)


def test_no_volume_means_no_fill() -> None:
    dead = OhlcBar(100, 100, 100, 100, 0)
    assert fill_bar(dead, index=3, side="BUY", quantity=10, limit=None, tif="DAY", volume_cap=1e9) is None


def test_fok_is_all_or_nothing() -> None:
    assert fill_bar(BAR, index=3, side="BUY", quantity=20_000, limit=None, tif="FOK", volume_cap=1e9) is None
    full = fill_bar(BAR, index=3, side="BUY", quantity=500, limit=None, tif="FOK", volume_cap=1e9)
    assert full is not None and full.quantity == 500


def test_partial_fill_capped_by_volume() -> None:
    f = fill_bar(BAR, index=3, side="BUY", quantity=20_000, limit=None, tif="IOC", volume_cap=1_000)
    assert f is not None and f.quantity == 1_000


def test_limit_not_reached_no_fill_and_receipt_names_basis() -> None:
    assert fill_bar(BAR, index=3, side="BUY", quantity=10, limit=90.0, tif="GTC", volume_cap=1e9) is None
    f = fill_bar(BAR, index=3, side="BUY", quantity=10, limit=101.0, tif="GTC", volume_cap=1e9)
    assert f is not None and f.receipt()["fidelity"] == "BAR"
    assert f.receipt()["mode"] == "PAPER_SIMULATED"


def test_costs_are_explicit_and_non_negative() -> None:
    assert charge_costs(Decimal("100000"), commission_bps=Decimal("3")) == Decimal("30")
    with pytest.raises(FillError):
        charge_costs(Decimal("1"), commission_bps=Decimal("-1"))
