from datetime import datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.risk_sizing import RiskSizingError, size_trade_plan
from financial_brain.kernel.trader_signal import EntryCondition, ExitTrigger, PriceBasis, TradePlan
from financial_brain.kernel.units import BasisPoints, INRNotional, WholeUnits


def _plan(*, side="BUY", lot=1, precision=0, quantity=100):
    return TradePlan(
        instrument_id="NSE:INFY", side=side, price_basis=PriceBasis.UNDERLYING,
        entry_condition=EntryCondition.LIMIT_ZONE, entry_low=Decimal("100"),
        entry_high=Decimal("100"), stop_price=Decimal("90" if side == "BUY" else "110"),
        target_price=Decimal("120" if side == "BUY" else "80"), invalidation="support break",
        max_holding_sessions=5, tick_size=Decimal("0.05"), lot_size=Decimal(str(lot)),
        unit_precision=precision, quantity=Decimal(str(quantity)),
        entry_expires_at=datetime(2026, 11, 21, 11, tzinfo=timezone.utc),
        exit_triggers=(ExitTrigger.HARD_STOP, ExitTrigger.TAKE_PROFIT),
        paper_execution_eligible=True,
    )


def _size(plan, **overrides):
    values = {
        "risk_budget_inr": Decimal("500"), "available_capital_inr": Decimal("50000"),
        "max_notional_inr": Decimal("10000"), "depth_available_units": Decimal("100"),
        "max_depth_participation": Decimal("0.10"),
        "correlated_exposure_room_inr": Decimal("10000"),
    }
    return size_trade_plan(plan, **(values | overrides))


def test_size_uses_the_tightest_stop_capital_notional_depth_and_correlation_limit():
    result = _size(_plan())
    assert result.eligible
    assert result.quantity == Decimal("10")
    assert result.planned_loss_inr == Decimal("100")
    assert result.notional_inr == Decimal("1000")
    assert result.binding_limits == ("DEPTH",)


def test_risk_budget_is_respected_and_quantity_rounds_to_contract_lot():
    result = _size(
        _plan(lot=25, quantity=100), risk_budget_inr=Decimal("900"),
        available_capital_inr=Decimal("100000"), max_notional_inr=Decimal("100000"),
        depth_available_units=Decimal("1000"), max_depth_participation=Decimal("1"),
        correlated_exposure_room_inr=Decimal("100000"),
    )
    assert result.quantity == Decimal("75")
    assert result.planned_loss_inr == Decimal("750")
    assert result.binding_limits == ("STOP_RISK",)


def test_short_sizing_uses_stop_distance_and_zero_feasible_lots_is_ineligible():
    short = _size(_plan(side="SELL_SHORT"))
    assert short.quantity == Decimal("10")
    blocked = _size(_plan(lot=25), risk_budget_inr=Decimal("50"))
    assert not blocked.eligible
    assert blocked.quantity == 0
    assert blocked.reasons == ("NO_FEASIBLE_LOT_WITHIN_LIMITS",)


def test_invalid_depth_participation_and_unrepresentable_lot_precision_fail():
    with pytest.raises(RiskSizingError, match="between 0 and 1"):
        _size(_plan(), max_depth_participation=Decimal("1.1"))
    with pytest.raises(RiskSizingError, match="unit_precision"):
        _size(_plan(lot="0.05", precision=0))


def test_sizing_accepts_nominal_inr_depth_and_basis_point_limits():
    result = _size(
        _plan(), risk_budget_inr=INRNotional(Decimal("500")),
        available_capital_inr=INRNotional(Decimal("50000")),
        max_notional_inr=INRNotional(Decimal("10000")),
        depth_available_units=WholeUnits(Decimal("100")),
        max_depth_participation=BasisPoints(Decimal("1000")),
        correlated_exposure_room_inr=INRNotional(Decimal("10000")),
    )
    assert result.quantity == Decimal("10")
    assert result.binding_limits == ("DEPTH",)
