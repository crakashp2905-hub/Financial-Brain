from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.signals.contract import Signal, SignalSide, SignalValidationError

NOW = datetime(2026, 11, 23, 10, tzinfo=timezone.utc)
HASH = "e" * 64


def make_signal(**changes):
    values = {"signal_id": "sig-1", "desk_id": "swing", "strategy_id": "momentum",
              "strategy_version": "v1", "strategy_hash": HASH, "instrument_id": "NSE:INFY",
              "side": SignalSide.BUY, "as_of": NOW - timedelta(minutes=1),
              "decision_at": NOW, "expires_at": NOW + timedelta(minutes=5),
              "evidence_tier": "PAPER", "source_hash": HASH}
    return Signal(**(values | changes))


def test_signal_is_immutable_and_serializes_required_provenance():
    signal = make_signal()
    assert signal.as_dict()["side"] == "BUY"
    assert signal.as_dict()["strategy_hash"] == HASH
    with pytest.raises(FrozenInstanceError):
        signal.side = SignalSide.SELL


@pytest.mark.parametrize("changes", [
    {"strategy_hash": "bad"},
    {"as_of": NOW + timedelta(seconds=1)},
    {"expires_at": NOW},
    {"decision_at": datetime(2026, 11, 23, 10)},
    {"instrument_id": " "},
])
def test_signal_rejects_missing_or_unverifiable_contract_fields(changes):
    with pytest.raises(SignalValidationError):
        make_signal(**changes)
