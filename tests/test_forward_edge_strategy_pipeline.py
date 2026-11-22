from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from financial_brain.forward_edge.strategy_adapter import CandidateSignal
from financial_brain.forward_edge.strategy_pipeline import (
    StrategyPipelineError,
    execute_candidate,
)
from financial_brain.kernel.registry import StrategyEligibility, StrategySpec


def _spec() -> StrategySpec:
    return StrategySpec(
        "orb", "v1", {"window": 15}, "owner", family="session_breakout",
        code_sha256="a" * 64, asset_scope=("NSE_EQ",), decision_clock="bar_close",
        direction="LONG", stop_exit="range_low", horizon="intraday",
        data_requirements=("ohlcv_1m",),
    )


def _candidate() -> CandidateSignal:
    return CandidateSignal(
        "orb", "v1", "INFY", "BUY", 10, "decision-1",
        "2026-11-15T04:00:00Z", Decimal("1500"), {"strength": 0.8},
    )


def _eligibility(spec: StrategySpec, status: str = "PAPER_ELIGIBLE") -> StrategyEligibility:
    return StrategyEligibility(
        spec.strategy_id, spec.version, status, "tests/frozen-orb.json", "b" * 64,
        "paper-eligible review", spec.hash(),
    )


class _Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def decide(self, **kwargs: Any) -> bool:
        self.calls.append(("decide", kwargs))
        return True

    def record_decision_manifest(self, **kwargs: Any) -> bool:
        self.calls.append(("manifest", kwargs))
        return True

    def record_risk_verdict(self, **kwargs: Any) -> bool:
        self.calls.append(("risk", kwargs))
        return True

    def execute_paper_intent(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("execute", kwargs))
        return {"status": "FILLED", "order_id": kwargs["order_id"]}


def _kwargs(*, allowed: bool = True) -> dict[str, Any]:
    return {
        "session_id": "session-1",
        "spec": _spec(),
        "eligibility": _eligibility(_spec()),
        "signal": _candidate(),
        "input_event_ids": ("quote-1",),
        "manifest": {
            "decision_id": "decision-1",
            "selections": [{"symbol": "INFY", "quantity": 10}],
        },
        "risk": {
            "allowed": allowed,
            "verdict_id": "risk-1",
            "policy_id": "risk-v1",
            "policy_sha256": "c" * 64,
            "reason_codes": () if allowed else ("CAPACITY_LIMIT",),
            "assessed_at": datetime(2026, 11, 15, 4, 0, tzinfo=timezone.utc),
        },
        "execution": {
            "event_id": "quote-2",
            "limit_price": Decimal("1500"),
            "tick_size": Decimal("0.05"),
            "freeze_limit": 1000,
            "slice_size": 1000,
            "adverse_slippage_bps": Decimal("0"),
            "latency_ms": 100,
        },
    }


def test_approved_candidate_flows_through_versioned_paper_pipeline() -> None:
    runner = _Runner()
    result = execute_candidate(runner, **_kwargs())  # type: ignore[arg-type]

    assert [name for name, _ in runner.calls] == ["decide", "manifest", "risk", "execute"]
    decision = runner.calls[0][1]
    assert decision["outcome"] == "TRADE"
    assert decision["payload"]["candidate_intent"]["strategy_sha256"] == _spec().hash()
    order = runner.calls[-1][1]
    assert order["order_id"] == result.intent.intent_id
    assert order["decision_id"] == "decision-1"
    assert order["quantity"] == 10
    assert result.receipt == {"status": "FILLED", "order_id": result.intent.intent_id}


def test_risk_rejection_is_journaled_and_never_reaches_paper_oms() -> None:
    runner = _Runner()
    result = execute_candidate(runner, **_kwargs(allowed=False))  # type: ignore[arg-type]

    assert [name for name, _ in runner.calls] == ["decide", "manifest", "risk"]
    assert runner.calls[0][1]["outcome"] == "WITHHELD"
    assert runner.calls[0][1]["reason_codes"] == ("CAPACITY_LIMIT",)
    assert result.receipt is None


def test_candidate_requires_matching_frozen_paper_eligibility() -> None:
    runner = _Runner()
    kwargs = _kwargs()
    kwargs["eligibility"] = _eligibility(_spec(), status="SHADOW_ONLY")
    with pytest.raises(StrategyPipelineError, match="paper eligibility"):
        execute_candidate(runner, **kwargs)  # type: ignore[arg-type]
    assert runner.calls == []


def test_candidate_limit_cannot_be_replaced_after_signal_freeze() -> None:
    runner = _Runner()
    kwargs = _kwargs()
    kwargs["execution"]["limit_price"] = Decimal("1501")
    with pytest.raises(StrategyPipelineError, match="frozen reference price"):
        execute_candidate(runner, **kwargs)  # type: ignore[arg-type]
    assert runner.calls == []
