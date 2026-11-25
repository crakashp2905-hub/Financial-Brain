from __future__ import annotations

import pytest

from financial_brain.attribution.pipeline import (
    AttributionError,
    decompose_available_trader_layers,
    decompose_trader_layers,
)


def test_complete_trader_layer_attribution_adds_to_realized_return() -> None:
    report = decompose_trader_layers(
        strategy_selection=0.012, sizing=0.003, market_beta=0.008,
        timing=-0.002, execution=-0.001, taxes=-0.0005, other_costs=-0.0005,
    )
    assert report["complete"] is True
    assert report["total"] == pytest.approx(0.019)
    assert report["unit"] == "decimal_return_on_common_opening_capital"
    assert report["components_sum_exactly"] is True


def test_missing_attribution_component_is_not_silently_filled_with_zero() -> None:
    report = decompose_available_trader_layers(
        strategy_selection=0.01, sizing=0.002, market_beta=None,
        timing=0.0, execution=-0.001, taxes=-0.0002,
    )
    assert report["complete"] is False
    assert report["total"] is None
    assert report["missing_components"] == ["market_beta"]


def test_nonfinite_attribution_component_is_rejected() -> None:
    with pytest.raises(AttributionError, match="finite"):
        decompose_trader_layers(
            strategy_selection=float("nan"), sizing=0, market_beta=0,
            timing=0, execution=0, taxes=0,
        )
