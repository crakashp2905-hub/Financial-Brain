"""FE-0055 stable bridge parity for legacy and kernel paper intent projections."""
from decimal import Decimal

import pytest

from financial_brain.forward_edge.strategy_adapter import CandidateSignal, StrategyAdapterError, adapt_candidate
from financial_brain.kernel.registry import StrategySpec


def _spec() -> StrategySpec:
    return StrategySpec("orb", "v1", {"window": 15}, "owner", family="session_breakout",
                        code_sha256="a" * 64, asset_scope=("NSE_EQ",), decision_clock="bar_close",
                        direction="LONG", stop_exit="range_low", horizon="intraday",
                        data_requirements=("ohlcv_1m",))


def _signal() -> CandidateSignal:
    return CandidateSignal("orb", "v1", "INFY", "BUY", 10, "decision-1",
                           "2026-11-15T04:00:00Z", Decimal("1500.00"), {"strength": 0.8})


def test_same_candidate_produces_equivalent_versioned_engine_intents() -> None:
    intent_a = adapt_candidate(_signal(), _spec())
    intent_b = adapt_candidate(_signal(), _spec())
    assert intent_a.as_dict() == intent_b.as_dict()
    legacy = intent_a.legacy_paper()
    kernel = intent_a.kernel_oms(account="paper:orb", reserve=Decimal("15000"))
    assert (legacy["symbol"], legacy["side"], legacy["qty"], legacy["id"]) == (
        kernel["client_id"] and "INFY", kernel["side"], kernel["quantity"], kernel["client_id"])
    assert kernel["limit"] == Decimal("1500.00")
    assert intent_a.adapter_version == "fb-paper-adapter.v1"


def test_adapter_rejects_spec_mismatch_or_missing_forward_contract() -> None:
    signal = _signal()
    with pytest.raises(StrategyAdapterError, match="identity"):
        adapt_candidate(CandidateSignal("other", "v1", signal.symbol, signal.side, signal.quantity,
                                        signal.decision_id, signal.decision_time_utc,
                                        signal.reference_price, signal.signal_payload), _spec())
    incomplete = StrategySpec("orb", "v1", {}, "owner")
    with pytest.raises(StrategyAdapterError, match="not forward-valid"):
        adapt_candidate(signal, incomplete)


def test_intent_identity_changes_with_decision_or_signal_payload() -> None:
    original = adapt_candidate(_signal(), _spec())
    changed = _signal()
    changed = CandidateSignal(changed.strategy_id, changed.strategy_version, changed.symbol, changed.side,
                              changed.quantity, changed.decision_id, changed.decision_time_utc,
                              changed.reference_price, {"strength": 0.9})
    assert adapt_candidate(changed, _spec()).intent_id != original.intent_id
