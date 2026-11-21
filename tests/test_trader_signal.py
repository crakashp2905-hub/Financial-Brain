from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.trader_signal import (
    DecisionAssessment, DecisionFeature, DeskId, EvidenceTier,
    EntryCondition, ExecutionIntent, ExecutionIntentType,
    ExpectedReturnDistribution, SignalContractError, SignalSide, TradePlan, TraderSignal,
    SignalLifecycle, SignalLifecycleEvent, SignalState,
)


def _signal(**changes):
    now = datetime(2026, 11, 21, 10, tzinfo=timezone.utc)
    values = {
        "signal_id": "550e8400-e29b-41d4-a716-446655440000",
        "desk_id": DeskId.SWING,
        "strategy_id": "momentum",
        "strategy_version": "2.1.0",
        "strategy_hash": "a" * 64,
        "instrument_id": "NSE:INFY",
        "side": SignalSide.BUY,
        "as_of": now - timedelta(minutes=1),
        "decision_at": now,
        "expires_at": now + timedelta(minutes=5),
        "evidence_tier": EvidenceTier.LOCAL_PAPER,
        "source_hash": "b" * 64,
    }
    return TraderSignal(**(values | changes))


def test_canonical_signal_is_typed_frozen_and_has_strategy_source_lineage():
    signal = _signal()
    assert signal.desk_id is DeskId.SWING
    assert signal.side is SignalSide.BUY
    with pytest.raises(FrozenInstanceError):
        signal.side = SignalSide.SELL


@pytest.mark.parametrize("changes, message", [
    ({"signal_id": "not-a-uuid"}, "UUID"),
    ({"strategy_hash": "x" * 64}, "SHA-256"),
    ({"source_hash": "A" * 64}, "SHA-256"),
    ({"as_of": datetime(2026, 11, 21)}, "timezone-aware"),
    ({"as_of": datetime(2026, 11, 21, 10, 0, 1, tzinfo=timezone.utc)}, "later than"),
    ({"expires_at": datetime(2026, 11, 21, 10, tzinfo=timezone.utc)}, "later than"),
    ({"instrument_id": " "}, "instrument_id"),
])
def test_invalid_identity_hash_and_clock_values_fail(changes, message):
    with pytest.raises(SignalContractError, match=message):
        _signal(**changes)


def test_assessment_carries_asof_features_distribution_cost_risk_and_benchmark():
    now = datetime(2026, 11, 21, 10, tzinfo=timezone.utc)
    feature = DecisionFeature("momentum_20d", Decimal("0.12"), now, "c" * 64)
    assessment = DecisionAssessment(
        signal_id="550e8400-e29b-41d4-a716-446655440000", decision_at=now,
        features=(feature,), evidence=(feature,),
        expected_return=ExpectedReturnDistribution(
            Decimal("0.03"), Decimal("-0.08"), Decimal("0.11"), Decimal("0.9"), "20 sessions",
        ),
        benchmark_id="NIFTY_50_TRI@2026-10", estimated_cost_bps=Decimal("12"),
        risk_budget_inr=Decimal("5000"), expected_return_inr=Decimal("1800"),
        contraindications=("NONE",),
    )
    assert assessment.expected_return.lower_quantile == Decimal("-0.08")
    assert assessment.estimated_cost_bps == Decimal("12")


def test_assessment_rejects_lookahead_and_invalid_distribution():
    now = datetime(2026, 11, 21, 10, tzinfo=timezone.utc)
    future = DecisionFeature("close", Decimal("100"), now + timedelta(seconds=1), "d" * 64)
    with pytest.raises(SignalContractError, match="future"):
        DecisionAssessment(
            signal_id="550e8400-e29b-41d4-a716-446655440000", decision_at=now,
            features=(future,), evidence=(future,),
            expected_return=ExpectedReturnDistribution(
                Decimal("0"), Decimal("-1"), Decimal("1"), Decimal("0.9"), "1d",
            ), benchmark_id="NIFTY", estimated_cost_bps=Decimal("1"),
            risk_budget_inr=Decimal("10"), expected_return_inr=Decimal("1"),
            contraindications=("NONE",),
        )
    with pytest.raises(SignalContractError, match="quantiles"):
        ExpectedReturnDistribution(
            Decimal("2"), Decimal("-1"), Decimal("1"), Decimal("0.9"), "1d",
        )


def test_execution_intents_cover_exchange_mf_and_no_trade_without_mixing_units():
    ids = {"intent_id": "550e8400-e29b-41d4-a716-446655440001",
           "signal_id": "550e8400-e29b-41d4-a716-446655440000"}
    exchange = ExecutionIntent(
        **ids, intent_type=ExecutionIntentType.EXCHANGE_LIMIT, instrument_id="NSE:INFY",
        quantity=Decimal("2"), limit_price=Decimal("1500"),
    )
    marketable = ExecutionIntent(
        intent_id="550e8400-e29b-41d4-a716-446655440002",
        signal_id=ids["signal_id"], intent_type=ExecutionIntentType.EXCHANGE_MARKETABLE_SIMULATION,
        instrument_id="NSE:INFY", quantity=Decimal("2"), quote_hash="e" * 64,
    )
    mf = ExecutionIntent(
        intent_id="550e8400-e29b-41d4-a716-446655440003",
        signal_id=ids["signal_id"], intent_type=ExecutionIntentType.MF_SUBSCRIBE,
        instrument_id="MF:123456", amount_inr=Decimal("5000"),
    )
    no_trade = ExecutionIntent(
        intent_id="550e8400-e29b-41d4-a716-446655440004",
        signal_id=ids["signal_id"], intent_type=ExecutionIntentType.NO_TRADE,
        reason="costs exceed expected edge",
    )
    assert [exchange.intent_type, marketable.intent_type, mf.intent_type, no_trade.intent_type] == [
        ExecutionIntentType.EXCHANGE_LIMIT,
        ExecutionIntentType.EXCHANGE_MARKETABLE_SIMULATION,
        ExecutionIntentType.MF_SUBSCRIBE,
        ExecutionIntentType.NO_TRADE,
    ]


def test_execution_intents_reject_mixed_exchange_and_mf_fields():
    common = {"intent_id": "550e8400-e29b-41d4-a716-446655440001",
              "signal_id": "550e8400-e29b-41d4-a716-446655440000",
              "instrument_id": "NSE:INFY"}
    with pytest.raises(SignalContractError, match="quantity only"):
        ExecutionIntent(
            **common, intent_type=ExecutionIntentType.EXCHANGE_LIMIT,
            quantity=Decimal("1"), amount_inr=Decimal("1500"), limit_price=Decimal("1500"),
        )
    with pytest.raises(SignalContractError, match="quote SHA-256"):
        ExecutionIntent(
            **common, intent_type=ExecutionIntentType.EXCHANGE_MARKETABLE_SIMULATION,
            quantity=Decimal("1"),
        )
    with pytest.raises(SignalContractError, match="NO_TRADE.*requires"):
        ExecutionIntent(
            intent_id=common["intent_id"], signal_id=common["signal_id"],
            intent_type=ExecutionIntentType.NO_TRADE,
        )


def test_trade_plan_carries_entry_exit_invalidation_horizon_precision_and_eligibility():
    plan = TradePlan(
        instrument_id="NSE:INFY", side="BUY", entry_condition=EntryCondition.PULLBACK_CONFIRMATION,
        entry_low=Decimal("1480"), entry_high=Decimal("1490"), stop_price=Decimal("1450"),
        target_price=Decimal("1570"), invalidation="daily close below support",
        max_holding_sessions=12, tick_size=Decimal("0.05"), lot_size=Decimal("1"),
        unit_precision=0, paper_execution_eligible=True,
    )
    assert plan.entry_low < plan.entry_high < plan.target_price


@pytest.mark.parametrize("changes, message", [
    ({"stop_price": Decimal("1485")}, "long stop and target"),
    ({"target_price": Decimal("100")}, "long stop and target"),
    ({"side": "SELL_SHORT", "target_price": Decimal("1570")}, "short target and stop"),
    ({"tick_size": Decimal("0")}, "tick_size"),
    ({"paper_execution_eligible": False}, "requires a reason"),
    ({"max_holding_sessions": 0}, "max_holding_sessions"),
])
def test_trade_plan_rejects_inconsistent_or_unexecutable_conditions(changes, message):
    values = {
        "instrument_id": "NSE:INFY", "side": "BUY",
        "entry_condition": EntryCondition.LIMIT_ZONE,
        "entry_low": Decimal("100"), "entry_high": Decimal("101"),
        "stop_price": Decimal("95"), "target_price": Decimal("110"),
        "invalidation": "close below support", "max_holding_sessions": 5,
        "tick_size": Decimal("0.05"), "lot_size": Decimal("1"),
        "unit_precision": 0, "paper_execution_eligible": True,
        "ineligibility_reason": None,
    }
    with pytest.raises(SignalContractError, match=message):
        TradePlan(**(values | changes))


def test_signal_lifecycle_enforces_transitions_time_order_and_idempotency():
    signal_id = "550e8400-e29b-41d4-a716-446655440000"
    t0 = datetime(2026, 11, 21, 10, tzinfo=timezone.utc)
    detected = SignalLifecycle(signal_id, (
        SignalLifecycleEvent(SignalState.DETECTED, t0, "detect-1"),
    ))
    eligible_event = SignalLifecycleEvent(SignalState.ELIGIBLE, t0 + timedelta(seconds=1), "elig-1")
    eligible = detected.advance(eligible_event)
    assert eligible.advance(eligible_event) is eligible
    approved = eligible.advance(SignalLifecycleEvent(
        SignalState.APPROVED, t0 + timedelta(seconds=2), "approve-1",
    ))
    submitted = approved.advance(SignalLifecycleEvent(
        SignalState.SUBMITTED, t0 + timedelta(seconds=3), "submit-1",
    ))
    partial = submitted.advance(SignalLifecycleEvent(
        SignalState.PARTIAL, t0 + timedelta(seconds=4), "fill-1",
    ))
    closed = partial.advance(SignalLifecycleEvent(
        SignalState.FILLED, t0 + timedelta(seconds=5), "fill-2",
    )).advance(SignalLifecycleEvent(
        SignalState.EXITED, t0 + timedelta(seconds=6), "exit-1",
    ))
    assert closed.state is SignalState.EXITED
    assert len(closed.digest) == 64
    with pytest.raises(SignalContractError, match="different event"):
        eligible.advance(SignalLifecycleEvent(
            SignalState.ELIGIBLE, t0 + timedelta(seconds=2), "elig-1",
        ))


@pytest.mark.parametrize("next_state, reason", [
    (SignalState.SUBMITTED, None),
    (SignalState.FILLED, None),
])
def test_signal_lifecycle_rejects_skipped_states(next_state, reason):
    t0 = datetime(2026, 11, 21, 10, tzinfo=timezone.utc)
    lifecycle = SignalLifecycle("550e8400-e29b-41d4-a716-446655440000", (
        SignalLifecycleEvent(SignalState.DETECTED, t0, "detect"),
    ))
    with pytest.raises(SignalContractError, match="invalid signal transition"):
        lifecycle.advance(SignalLifecycleEvent(next_state, t0 + timedelta(seconds=1), "next", reason))


def test_signal_lifecycle_rejects_backwards_event_time():
    t0 = datetime(2026, 11, 21, 10, tzinfo=timezone.utc)
    lifecycle = SignalLifecycle("550e8400-e29b-41d4-a716-446655440000", (
        SignalLifecycleEvent(SignalState.DETECTED, t0, "detect"),
        SignalLifecycleEvent(SignalState.ELIGIBLE, t0 + timedelta(seconds=2), "eligible"),
    ))
    with pytest.raises(SignalContractError, match="move backwards"):
        lifecycle.advance(SignalLifecycleEvent(
            SignalState.SHADOW, t0 + timedelta(seconds=1), "shadow",
        ))
