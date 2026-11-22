"""Versioned candidate-to-paper execution pipeline (FB-0801).

This adapter joins an approved strategy candidate to the existing durable
forward-session decision, risk, manifest, OMS, fill, and account projections.
It is paper-only and never routes to a broker.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from financial_brain.forward_edge.strategy_adapter import (
    CandidateSignal,
    CanonicalIntent,
    StrategyAdapterError,
    adapt_candidate,
)
from financial_brain.kernel.registry import StrategyEligibility, StrategySpec

if TYPE_CHECKING:
    from financial_brain.forward_edge.session_runner import ForwardSessionRunner


class StrategyPipelineError(ValueError):
    """A candidate cannot safely traverse the canonical paper pipeline."""


@dataclass(frozen=True)
class CandidateExecution:
    intent: CanonicalIntent
    receipt: dict[str, Any] | None


def execute_candidate(
    runner: ForwardSessionRunner,
    *,
    session_id: str,
    spec: StrategySpec,
    eligibility: StrategyEligibility,
    signal: CandidateSignal,
    input_event_ids: tuple[str, ...],
    manifest: dict[str, Any],
    risk: dict[str, Any],
    execution: dict[str, Any],
) -> CandidateExecution:
    """Record a frozen candidate through the canonical paper OMS lifecycle.

    ``execution`` contains only the existing runner's market/execution controls
    (quote event, limit, tick, latency, and fill constraints). Identity fields
    are derived from the canonical candidate and cannot be overridden.

    Each step is persisted by the runner's idempotent journal/outbox. Calling
    this function again after a process restart reuses the same decision and
    order identities; conflicting retries fail closed in the underlying store.
    A risk rejection is recorded as WITHHELD and cannot reach the OMS.
    """
    if (
        eligibility.strategy_id != spec.strategy_id
        or eligibility.version != spec.version
        or eligibility.spec_sha256 != spec.hash()
        or eligibility.status not in {"PAPER_ELIGIBLE", "FROZEN_CHALLENGER", "VERIFIED"}
    ):
        raise StrategyPipelineError("candidate requires matching frozen paper eligibility")
    try:
        config = runner.state_store.config(session_id)
    except (AttributeError, KeyError, ValueError) as exc:
        raise StrategyPipelineError("candidate session has no readable frozen strategy eligibility") from exc
    frozen_rows = config.constraints.get("strategy_eligibilities", [])
    if not isinstance(frozen_rows, list):
        raise StrategyPipelineError("session strategy eligibility must be a frozen list")
    matching_rows = [
        row for row in frozen_rows
        if isinstance(row, dict)
        and row.get("strategy_id") == spec.strategy_id
        and row.get("version") == spec.version
    ]
    if matching_rows != [asdict(eligibility)]:
        raise StrategyPipelineError("candidate eligibility does not match the session's frozen eligibility")
    try:
        intent = adapt_candidate(signal, spec)
    except StrategyAdapterError as exc:
        raise StrategyPipelineError(str(exc)) from exc

    try:
        decided_at = datetime.fromisoformat(signal.decision_time_utc.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StrategyPipelineError("candidate decision time must be ISO-8601") from exc
    if decided_at.tzinfo is None or decided_at.utcoffset() is None:
        raise StrategyPipelineError("candidate decision time must include a timezone")
    if not input_event_ids:
        raise StrategyPipelineError("candidate must cite at least one input market event")
    selected = next(
        (row for row in manifest.get("selections", [])
         if isinstance(row, dict) and row.get("symbol") == signal.symbol),
        None,
    )
    if selected is None or signal.quantity > selected.get("quantity", 0):
        raise StrategyPipelineError("candidate must match a funded manifest selection")
    if manifest.get("decision_id") != signal.decision_id:
        raise StrategyPipelineError("manifest decision identity differs from the candidate")
    allowed = risk.get("allowed")
    if type(allowed) is not bool:
        raise StrategyPipelineError("risk verdict must contain an explicit boolean")
    reason_codes = tuple(str(code) for code in risk.get("reason_codes", ()))
    if not allowed and not reason_codes:
        raise StrategyPipelineError("risk rejection requires at least one reason code")
    if allowed:
        required_execution_fields = {
            "event_id", "limit_price", "tick_size", "freeze_limit", "slice_size",
            "adverse_slippage_bps", "latency_ms",
        }
        if required_execution_fields - execution.keys():
            missing = ", ".join(sorted(required_execution_fields - execution.keys()))
            raise StrategyPipelineError(f"execution controls are missing: {missing}")
        if {"session_id", "order_id", "decision_id", "side", "quantity", "quantity_unit"} & execution.keys():
            raise StrategyPipelineError("execution cannot override canonical candidate identity")
        if Decimal(str(execution["limit_price"])) != signal.reference_price:
            raise StrategyPipelineError("paper limit must match the candidate's frozen reference price")

    runner.decide(
        session_id=session_id,
        strategy_id=spec.strategy_id,
        decision_id=signal.decision_id,
        outcome="TRADE" if allowed else "WITHHELD",
        input_event_ids=input_event_ids,
        decided_at=decided_at,
        reason_codes=() if allowed else reason_codes,
        payload={
            "candidate_intent": intent.as_dict(),
            "eligibility": {
                "status": eligibility.status,
                "source_ref": eligibility.source_ref,
                "source_sha256": eligibility.source_sha256,
                "spec_sha256": eligibility.spec_sha256,
            },
        },
    )
    runner.record_decision_manifest(
        session_id=session_id, decision_id=signal.decision_id, manifest=manifest
    )
    runner.record_risk_verdict(
        session_id=session_id,
        decision_id=signal.decision_id,
        verdict_id=str(risk.get("verdict_id", f"{signal.decision_id}:risk")),
        allowed=allowed,
        policy_id=str(risk.get("policy_id", "")),
        policy_sha256=str(risk.get("policy_sha256", "")),
        reason_codes=reason_codes,
        assessed_at=risk.get("assessed_at", decided_at),
    )
    if not allowed:
        return CandidateExecution(intent=intent, receipt=None)
    receipt = runner.execute_paper_intent(
        session_id=session_id,
        order_id=intent.intent_id,
        decision_id=signal.decision_id,
        side=signal.side,
        quantity=signal.quantity,
        quantity_unit=signal.quantity_unit,
        **execution,
    )
    return CandidateExecution(intent=intent, receipt=receipt)
