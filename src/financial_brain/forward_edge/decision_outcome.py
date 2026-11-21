"""Unified explicit trade/NO_TRADE decision and abstention journaling (FE-0113)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite

from financial_brain.forward_edge.data_gate import DataGateResult
from financial_brain.forward_edge.decision_journal import DecisionJournal, DecisionRecord


@dataclass(frozen=True)
class ForwardDecision:
    decision: str
    reason_codes: tuple[str, ...]
    requested_notional: float
    after_cost_alpha_bps: float | None


def evaluate_forward_decision(*, market_data: DataGateResult,
                              requested_notional: float,
                              visible_liquidity_notional: float | None,
                              expected_gross_alpha_bps: float | None,
                              estimated_all_in_cost_bps: float | None,
                              margin_is_certain: bool,
                              risk_approved: bool) -> ForwardDecision:
    """Return TRADE eligibility only when every required decision input supports it.

    This function authorizes neither an order nor a fill. It turns missing/unsafe inputs
    into durable, reason-coded abstentions at the policy boundary.
    """
    if not isfinite(requested_notional) or requested_notional <= 0:
        raise ValueError("requested_notional must be finite and positive")
    reasons = {str(reason) for reason in market_data.reasons}
    if market_data.decision != "TRADE":
        reasons.add("MARKET_DATA_INELIGIBLE")
    if visible_liquidity_notional is None:
        reasons.add("LIQUIDITY_UNKNOWN")
    elif not isfinite(visible_liquidity_notional) or visible_liquidity_notional < 0:
        raise ValueError("visible_liquidity_notional must be finite and non-negative")
    elif visible_liquidity_notional < requested_notional:
        reasons.add("INSUFFICIENT_LIQUIDITY")
    if not margin_is_certain:
        reasons.add("MARGIN_UNCERTAIN")
    if not risk_approved:
        reasons.add("RISK_NOT_APPROVED")
    alpha: float | None = None
    if expected_gross_alpha_bps is None:
        reasons.add("ALPHA_UNKNOWN")
    elif not isfinite(expected_gross_alpha_bps):
        raise ValueError("expected_gross_alpha_bps must be finite when provided")
    if estimated_all_in_cost_bps is None:
        reasons.add("COST_UNKNOWN")
    elif not isfinite(estimated_all_in_cost_bps) or estimated_all_in_cost_bps < 0:
        raise ValueError("estimated_all_in_cost_bps must be finite and non-negative")
    if expected_gross_alpha_bps is not None and estimated_all_in_cost_bps is not None:
        alpha = expected_gross_alpha_bps - estimated_all_in_cost_bps
        if alpha <= 0:
            reasons.add("NON_POSITIVE_AFTER_COST_ALPHA")
    ordered = tuple(sorted(str(reason) for reason in reasons))
    return ForwardDecision("NO_TRADE" if ordered else "TRADE", ordered,
                           requested_notional, alpha)


def journal_abstention(*, journal: DecisionJournal, session_id: str, strategy_id: str,
                       decision_id: str, decided_at: datetime, input_sha256: str,
                       decision: ForwardDecision) -> bool:
    """Persist a no-trade outcome; explicitly records that no fallback fill occurred."""
    if decision.decision != "NO_TRADE" or not decision.reason_codes:
        raise ValueError("only a reasoned NO_TRADE decision can be journaled")
    return journal.append(DecisionRecord(
        session_id=session_id, strategy_id=strategy_id, decision_id=decision_id,
        outcome="NO_SIGNAL", decided_at=decided_at, reason_codes=decision.reason_codes,
        input_sha256=input_sha256,
        payload={"decision": "NO_TRADE", "requested_notional": decision.requested_notional,
                 "after_cost_alpha_bps": decision.after_cost_alpha_bps,
                 "fallback_fill": False}))
