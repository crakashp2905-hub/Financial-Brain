from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.kernel.trader_signal import (
    DeskId, EvidenceTier, SignalContractError, SignalSide, TraderSignal,
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
