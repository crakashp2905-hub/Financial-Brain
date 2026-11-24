from datetime import datetime, timedelta, timezone
from dataclasses import replace
from decimal import Decimal

import pytest

from financial_brain.kernel.entry_constraints import (
    EntryConstraintPolicy,
    EntryConstraintReason,
    EntryConstraintState,
    evaluate_entry_constraints,
)
from financial_brain.kernel.execution_feasibility import ExecutionEvidence
from financial_brain.kernel.trader_signal import (
    DeskId, EntryCondition, EvidenceTier, ExitTrigger, PriceBasis, SignalSide, TradePlan, TraderSignal,
)


DECISION = datetime(2026, 11, 23, 18, 30, tzinfo=timezone.utc)
HASH = "a" * 64


def _signal(*, expires=DECISION + timedelta(minutes=5)):
    return TraderSignal(
        signal_id="4dbe4c48-7104-4f47-b84c-14a55cd49f24", desk_id=DeskId.SWING,
        strategy_id="momentum", strategy_version="v1", strategy_hash=HASH,
        instrument_id="NSE:INFY", side=SignalSide.BUY, as_of=DECISION - timedelta(seconds=1),
        decision_at=DECISION, expires_at=expires, evidence_tier=EvidenceTier.REPLAY,
        source_hash=HASH,
    )


def _plan(*, expires=DECISION + timedelta(minutes=5)):
    return TradePlan(
        instrument_id="NSE:INFY", side="BUY", price_basis=PriceBasis.UNDERLYING,
        entry_condition=EntryCondition.LIMIT_ZONE, entry_low=Decimal("100"),
        entry_high=Decimal("101"), stop_price=Decimal("95"), target_price=Decimal("110"),
        invalidation="close below support", max_holding_sessions=2,
        tick_size=Decimal("0.05"), lot_size=Decimal("1"), unit_precision=0,
        quantity=Decimal("10"), entry_expires_at=expires,
        exit_triggers=(ExitTrigger.HARD_STOP, ExitTrigger.TAKE_PROFIT),
        paper_execution_eligible=True,
    )


def _policy(**changes):
    values = dict(policy_id="swing-entry", version="v1", max_signal_ttl_seconds=300,
                  max_spread_bps=Decimal("20"), max_gap_bps=Decimal("50"),
                  max_slippage_bps=Decimal("10"))
    return EntryConstraintPolicy(**(values | changes))


def _execution(*, now=DECISION, ask=Decimal("100.1"), spread=Decimal("10"),
               slippage=Decimal("2"), max_age=500):
    event = now - timedelta(milliseconds=100)
    received = now - timedelta(milliseconds=90)
    return ExecutionEvidence(
        bid=Decimal("100"), ask=ask, quote_age_ms=90, max_quote_age_ms=max_age,
        spread_bps=spread, max_spread_bps=Decimal("25"),
        estimated_slippage_bps=slippage, max_slippage_bps=Decimal("15"),
        visible_quantity=Decimal("20"), required_quantity=Decimal("10"),
        quote_source_id="KITE_READ_ONLY", quote_source_hash=HASH,
        quote_event_at=event, quote_received_at=received, observed_at=now,
    )


def _evaluate(*, now=DECISION, signal=None, plan=None, policy=None, execution=None):
    return evaluate_entry_constraints(
        signal=signal or _signal(), plan=plan or _plan(), policy=policy or _policy(),
        execution=execution or _execution(now=now), decision_reference_price=Decimal("100"),
        evaluated_at=now,
    )


def test_current_quote_inside_frozen_entry_zone_yields_hash_pinned_receipt():
    result = _evaluate()

    assert result.state is EntryConstraintState.ELIGIBLE
    assert result.executable_entry_price == Decimal("100.1")
    assert result.adverse_gap_bps == Decimal("10.0")
    assert len(result.receipt_sha256) == 64


@pytest.mark.parametrize(
    ("execution", "policy", "reason"),
    [
        (_execution(ask=Decimal("102")), _policy(), EntryConstraintReason.ENTRY_ZONE_BREACHED),
        (_execution(ask=Decimal("100.4")), _policy(max_gap_bps=Decimal("20")), EntryConstraintReason.MAX_GAP_EXCEEDED),
        (_execution(spread=Decimal("30")), _policy(), EntryConstraintReason.MAX_SPREAD_EXCEEDED),
        (_execution(slippage=Decimal("12")), _policy(), EntryConstraintReason.MAX_SLIPPAGE_EXCEEDED),
    ],
)
def test_zone_gap_spread_and_slippage_limits_block_entry(execution, policy, reason):
    result = _evaluate(policy=policy, execution=execution)

    assert result.state is EntryConstraintState.REJECT
    assert reason in result.reasons


def test_expired_signal_requires_new_evaluation_and_cannot_late_fill():
    now = DECISION + timedelta(minutes=6)
    result = _evaluate(now=now)

    assert result.state is EntryConstraintState.EXPIRED
    assert result.executable_entry_price is None
    assert EntryConstraintReason.SIGNAL_EXPIRED_REQUIRES_REEVALUATION in result.reasons


def test_ttl_exceeding_policy_is_rejected_even_when_plan_is_valid():
    policy = _policy(max_signal_ttl_seconds=60)
    result = _evaluate(policy=policy)

    assert result.state is EntryConstraintState.REJECT
    assert EntryConstraintReason.SIGNAL_TTL_EXCEEDS_POLICY in result.reasons


def test_stale_quote_is_watch_and_missing_executable_side_never_passes():
    now = DECISION + timedelta(seconds=1)
    result = _evaluate(now=now, execution=_execution(now=DECISION, max_age=500))

    assert result.state is EntryConstraintState.WATCH
    assert EntryConstraintReason.STALE_OR_INCOMPLETE_QUOTE in result.reasons

    missing_ask = _evaluate(execution=replace(_execution(), ask=None))
    assert missing_ask.state is EntryConstraintState.WATCH
    assert EntryConstraintReason.STALE_OR_INCOMPLETE_QUOTE in missing_ask.reasons


def test_plan_must_match_signal_expiry():
    with pytest.raises(ValueError, match="expiry differs"):
        _evaluate(plan=_plan(expires=DECISION + timedelta(minutes=4)))
