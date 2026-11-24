from datetime import date, datetime, timedelta, timezone
from dataclasses import replace
from decimal import Decimal

import pytest

from financial_brain.costs.india import CostModel, Segment
from financial_brain.forward_edge.cost_schedule import CostSchedule
from financial_brain.kernel.entry_constraints import (
    EntryConstraintPolicy, evaluate_entry_constraints,
)
from financial_brain.kernel.execution_feasibility import ExecutionEvidence
from financial_brain.kernel.trade_economics_receipt import (
    CashBenchmark, TradeReceiptError, build_trade_economics_receipt,
)
from financial_brain.kernel.trader_signal import (
    DeskId, EntryCondition, EvidenceTier, ExitTrigger, PriceBasis, SignalSide, TradePlan,
    TraderSignal,
)


DECISION = datetime(2026, 11, 23, 18, 30, tzinfo=timezone.utc)
HASH = "a" * 64


def _signal(side=SignalSide.BUY):
    return TraderSignal(
        signal_id="4dbe4c48-7104-4f47-b84c-14a55cd49f24", desk_id=DeskId.SWING,
        strategy_id="momentum", strategy_version="v1", strategy_hash=HASH,
        instrument_id="NSE:INFY", side=side, as_of=DECISION - timedelta(seconds=1),
        decision_at=DECISION, expires_at=DECISION + timedelta(minutes=5),
        evidence_tier=EvidenceTier.REPLAY, source_hash=HASH,
    )


def _plan(side=SignalSide.BUY):
    return TradePlan(
        instrument_id="NSE:INFY", side=side.value, price_basis=PriceBasis.UNDERLYING,
        entry_condition=EntryCondition.LIMIT_ZONE, entry_low=Decimal("100"),
        entry_high=Decimal("101"), stop_price=Decimal("95" if side is SignalSide.BUY else "106"),
        target_price=Decimal("110" if side is SignalSide.BUY else "90"),
        invalidation="invalidated close", max_holding_sessions=3, tick_size=Decimal("0.05"),
        lot_size=Decimal("1"), unit_precision=0, quantity=Decimal("10"),
        entry_expires_at=DECISION + timedelta(minutes=5),
        exit_triggers=(ExitTrigger.HARD_STOP, ExitTrigger.TAKE_PROFIT),
        paper_execution_eligible=True,
    )


def _execution():
    return ExecutionEvidence(
        bid=Decimal("100"), ask=Decimal("100.1"), quote_age_ms=90, max_quote_age_ms=500,
        spread_bps=Decimal("10"), max_spread_bps=Decimal("20"),
        estimated_slippage_bps=Decimal("2"), max_slippage_bps=Decimal("10"),
        visible_quantity=Decimal("20"), required_quantity=Decimal("10"),
        quote_source_id="KITE_READ_ONLY", quote_source_hash=HASH,
        quote_event_at=DECISION - timedelta(milliseconds=100),
        quote_received_at=DECISION - timedelta(milliseconds=90), observed_at=DECISION,
    )


def _dependencies(signal=None, plan=None, execution=None):
    signal = signal or _signal()
    plan = plan or _plan()
    execution = execution or _execution()
    entry = evaluate_entry_constraints(
        signal=signal, plan=plan,
        policy=EntryConstraintPolicy("swing", "v1", 300, Decimal("20"),
                                     Decimal("50"), Decimal("10")),
        execution=execution, decision_reference_price=Decimal("100"), evaluated_at=DECISION,
    )
    schedule = CostSchedule(
        "paper-india", "v1", date(2026, 11, 24), None, Segment.DELIVERY, CostModel(),
        "tests/india-cost-schedule", HASH,
    )
    cash = CashBenchmark("treasury-cash", "v1", Decimal("0.05"), date(2026, 1, 1), None,
                         "tests/cash-benchmark", HASH)
    return signal, plan, execution, entry, schedule, cash


def test_receipt_items_costs_risk_breakeven_and_cash_opportunity():
    values = _dependencies()
    receipt = build_trade_economics_receipt(
        signal=values[0], plan=values[1], execution=values[2], entry_decision=values[3],
        cost_schedule=values[4], cash_benchmark=values[5], liquidity_bucket="large",
        holding_calendar_days=5,
    )

    assert receipt.gross_target_pnl_inr == Decimal("95.0")
    assert receipt.gross_stop_loss_inr == Decimal("55.0")
    assert receipt.total_target_cost_inr > 0
    assert receipt.net_target_pnl_inr == receipt.gross_target_pnl_inr - receipt.total_target_cost_inr
    assert receipt.risk_at_stop_inr == receipt.gross_stop_loss_inr + receipt.total_stop_cost_inr
    assert receipt.break_even_exit_price is not None and receipt.break_even_exit_price > receipt.entry_reference_price
    assert receipt.cash_opportunity_cost_inr > 0
    assert receipt.net_target_minus_cash_inr == receipt.net_target_pnl_inr - receipt.cash_opportunity_cost_inr
    assert len(receipt.cost_schedule_sha256) == len(receipt.receipt_sha256) == 64
    assert receipt.evidence_is_uncertain


def test_receipt_refuses_entry_decision_for_another_signal_or_noneligible_quote():
    values = _dependencies()
    invalid_entry = evaluate_entry_constraints(
        signal=values[0], plan=values[1],
        policy=EntryConstraintPolicy("swing", "v1", 300, Decimal("20"),
                                     Decimal("50"), Decimal("10")),
        execution=ExecutionEvidence(
            bid=Decimal("100"), ask=Decimal("103"), quote_age_ms=90, max_quote_age_ms=500,
            spread_bps=Decimal("10"), max_spread_bps=Decimal("20"),
            estimated_slippage_bps=Decimal("2"), max_slippage_bps=Decimal("10"),
            visible_quantity=Decimal("20"), required_quantity=Decimal("10"),
            quote_source_id="KITE_READ_ONLY", quote_source_hash=HASH,
            quote_event_at=DECISION - timedelta(milliseconds=100),
            quote_received_at=DECISION - timedelta(milliseconds=90), observed_at=DECISION,
        ), decision_reference_price=Decimal("100"), evaluated_at=DECISION,
    )
    with pytest.raises(TradeReceiptError, match="did not admit"):
        build_trade_economics_receipt(
            signal=values[0], plan=values[1], execution=values[2], entry_decision=invalid_entry,
            cost_schedule=values[4], cash_benchmark=values[5], liquidity_bucket="large",
            holding_calendar_days=5,
        )


def test_receipt_rejects_future_quote_and_unsupported_cost_bucket():
    values = _dependencies()
    future = replace(values[2], quote_event_at=DECISION + timedelta(seconds=1))
    with pytest.raises(TradeReceiptError, match="unavailable"):
        build_trade_economics_receipt(
            signal=values[0], plan=values[1], execution=future, entry_decision=values[3],
            cost_schedule=values[4], cash_benchmark=values[5], liquidity_bucket="large",
            holding_calendar_days=5,
        )
    with pytest.raises(TradeReceiptError, match="liquidity bucket"):
        build_trade_economics_receipt(
            signal=values[0], plan=values[1], execution=values[2], entry_decision=values[3],
            cost_schedule=values[4], cash_benchmark=values[5], liquidity_bucket="unknown",
            holding_calendar_days=5,
        )
