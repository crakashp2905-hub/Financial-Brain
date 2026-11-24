from datetime import datetime, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.trade_economics import (
    TradeEconomicsError, TradingCosts, estimate_trade_economics,
)
from financial_brain.kernel.trader_signal import EntryCondition, ExitTrigger, PriceBasis, TradePlan


def _plan(side="BUY"):
    return TradePlan(
        instrument_id="NSE:INFY", side=side, price_basis=PriceBasis.UNDERLYING,
        entry_condition=EntryCondition.LIMIT_ZONE, entry_low=Decimal("100"),
        entry_high=Decimal("100"), stop_price=Decimal("90" if side == "BUY" else "110"),
        target_price=Decimal("120" if side == "BUY" else "80"), invalidation="close beyond structure",
        max_holding_sessions=5, tick_size=Decimal("0.05"), lot_size=Decimal("1"),
        unit_precision=0, quantity=Decimal("10"),
        entry_expires_at=datetime(2026, 11, 21, 11, tzinfo=timezone.utc),
        exit_triggers=(ExitTrigger.HARD_STOP, ExitTrigger.TAKE_PROFIT),
        paper_execution_eligible=True,
    )


def test_estimated_net_reward_risk_includes_each_cost_and_labels_uncertainty():
    economics = estimate_trade_economics(
        _plan(), TradingCosts(Decimal("50"), Decimal("30"), Decimal("20"), Decimal("100")),
    )
    assert economics.planned_loss_inr == Decimal("100")
    assert economics.gross_reward_inr == Decimal("200")
    assert economics.gross_reward_to_risk == Decimal("2")
    assert economics.estimated_cost_inr == Decimal("200")
    assert economics.estimated_net_reward_inr == Decimal("0")
    assert economics.estimated_net_risk_inr == Decimal("300")
    assert economics.estimated_net_reward_to_risk == Decimal("0")
    assert economics.forecast_is_uncertain
    assert not economics.stop_loss_is_guaranteed


def test_costs_can_exceed_target_pnl_and_net_economics_show_negative_edge():
    economics = estimate_trade_economics(
        _plan(), TradingCosts(Decimal("100"), Decimal("50"), Decimal("50"), Decimal("25")),
    )
    assert economics.estimated_net_reward_inr == Decimal("-25")
    assert economics.estimated_net_reward_to_risk < 0


def test_short_trade_uses_correct_stop_and_target_direction():
    economics = estimate_trade_economics(_plan("SELL_SHORT"), TradingCosts(0, 0, 0, 0))
    assert economics.planned_loss_inr == Decimal("100")
    assert economics.gross_reward_inr == Decimal("200")
    assert economics.gross_reward_to_risk == Decimal("2")


def test_negative_costs_are_rejected():
    with pytest.raises(TradeEconomicsError, match="non-negative"):
        TradingCosts(Decimal("-1"), Decimal(0), Decimal(0), Decimal(0))
