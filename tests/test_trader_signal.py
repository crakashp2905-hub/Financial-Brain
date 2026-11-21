from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.trader_signal import (
    DecisionAssessment, DecisionFeature, DeskId, EvidenceTier,
    ExecutionIntent, ExecutionIntentType, ExpectedReturnDistribution,
    SignalContractError, SignalSide, TraderSignal,
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
