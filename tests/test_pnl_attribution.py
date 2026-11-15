import pytest

from financial_brain.kernel.pnl_attribution import AttributionError, COMPONENTS, reconcile_pnl_attribution
from financial_brain.kernel.units import Money


def inputs():
    values = {
        "realized_price_pnl": 30,
        "unrealized_price_pnl": 10,
        "carry_pnl": 5,
        "cash_yield_income": 2,
        "financing_cost": 3,
        "tax_cost": 1,
        "slippage_cost": 4,
        "trading_fees": 2,
    }
    return {
        "components": {name: Money.of(value, "INR") for name, value in values.items()},
        "source_hashes": {name: "a" * 64 for name in COMPONENTS},
        "nav_change": Money.of(40, "INR"),
        "external_flows": Money.of(3, "INR"),
    }


def test_attribution_bridge_reconciles_every_economic_component_and_external_flow():
    result = reconcile_pnl_attribution(**inputs())
    assert result["reconciled"] is True
    assert result["flow_adjusted_nav_change"] == "37"
    assert result["net_attributed_pnl"] == "37"
    assert result["rounding_residual"] == "0"


def test_attribution_refuses_missing_sources_currency_mismatch_and_unexplained_nav():
    data = inputs()
    del data["components"]["slippage_cost"]
    with pytest.raises(AttributionError, match="component set mismatch"):
        reconcile_pnl_attribution(**data)

    data = inputs()
    data["components"]["slippage_cost"] = Money.of(4, "USD")
    with pytest.raises(AttributionError, match="one reporting currency"):
        reconcile_pnl_attribution(**data)

    data = inputs()
    data["nav_change"] = Money.of(41, "INR")
    with pytest.raises(AttributionError, match="does not reconcile"):
        reconcile_pnl_attribution(**data)
