from __future__ import annotations

from decimal import Decimal

import pytest

from financial_brain.kernel.execution_model import (
    fx_all_in_cost,
    multileg_status,
    pessimistic_fill,
    queue_fill_estimate,
    within_price_band,
)


def test_queue_estimate_is_labelled_estimated() -> None:
    est = queue_fill_estimate(queue_ahead=300, volume_at_level=1000)
    assert est == {"fill_probability": pytest.approx(0.7), "label": "ESTIMATED"}
    assert queue_fill_estimate(500, 100)["fill_probability"] == 0.0


def test_price_band_refuses_moves_beyond_the_band() -> None:
    assert within_price_band(Decimal("104"), Decimal("100"), Decimal("0.05")) is True
    assert within_price_band(Decimal("106"), Decimal("100"), Decimal("0.05")) is False


def test_fx_all_in_cost_adds_swap_and_allows_credit() -> None:
    charged = fx_all_in_cost(Decimal("100000"), Decimal("1.5"), Decimal("10"), Decimal("20"), Decimal("2"), 5)
    assert charged == Decimal("15") + Decimal("20") + Decimal("10")
    credited = fx_all_in_cost(Decimal("100000"), Decimal("1.5"), Decimal("10"), Decimal("20"), Decimal("-2"), 5)
    assert credited == Decimal("15") + Decimal("20") - Decimal("10")


def test_multileg_partial_is_never_reported_as_filled() -> None:
    assert multileg_status([Decimal("10"), Decimal("10")], [Decimal("10"), Decimal("10")]) == "ALL_FILLED"
    assert multileg_status([Decimal("10"), Decimal("0")], [Decimal("10"), Decimal("10")]) == "PARTIAL"
    assert multileg_status([Decimal("0"), Decimal("0")], [Decimal("10"), Decimal("10")]) == "NONE"


def test_uncalibrated_fills_are_three_times_worse_and_adverse() -> None:
    cal_buy = pessimistic_fill(Decimal("100"), "BUY", Decimal("10"), calibrated=True)
    unc_buy = pessimistic_fill(Decimal("100"), "BUY", Decimal("10"), calibrated=False)
    assert cal_buy == Decimal("100.10")
    assert unc_buy == Decimal("100.30")
    assert pessimistic_fill(Decimal("100"), "SELL", Decimal("10"), calibrated=False) == Decimal("99.70")
