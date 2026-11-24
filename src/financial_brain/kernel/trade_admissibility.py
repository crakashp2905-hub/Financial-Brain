"""Fail-closed, reason-coded paper-trade admissibility over frozen evidence.

This module combines net expected value, execution quality, cohort identity, desk
hurdles and available paper capital. A TRADE result is only an admissible paper
candidate; it is never broker authorization.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from zoneinfo import ZoneInfo

from financial_brain.forward_edge.parameter_freeze import (
    ParameterFreezeError,
    StrategyFreezeRegistry,
)
from .execution_feasibility import (
    ExecutionEvidence,
    FeasibilityState,
    assess_execution_feasibility,
)
from .net_ev import NetEVEstimate
from .trade_economics_receipt import TradeEconomicsReceipt
from .trader_signal import SignalSide, TraderSignal


class AdmissibilityError(ValueError):
    """Invalid or inconsistent decision-time admissibility evidence."""


class DecisionState(StrEnum):
    TRADE = "TRADE"
    WATCH = "WATCH"
    REJECT = "REJECT"
    CASH = "CASH"


class DecisionReason(StrEnum):
    NO_EDGE = "NO_EDGE"
    STALE_FEED = "STALE_FEED"
    EXECUTION_NOT_FEASIBLE = "EXECUTION_NOT_FEASIBLE"
    INSTRUMENT_HALTED = "INSTRUMENT_HALTED"
    CONFIDENCE_TOO_LOW = "CONFIDENCE_TOO_LOW"
    CONFIDENCE_UNAVAILABLE = "CONFIDENCE_UNAVAILABLE"
    PORTFOLIO_UTILITY_UNAVAILABLE = "PORTFOLIO_UTILITY_UNAVAILABLE"
    MARGINAL_PORTFOLIO_HARM = "MARGINAL_PORTFOLIO_HARM"
    COSTS_EXCEED_GROSS_EDGE = "COSTS_EXCEED_GROSS_EDGE"
    NET_EDGE_BELOW_HURDLE = "NET_EDGE_BELOW_HURDLE"
    VOLATILITY_LIMIT = "VOLATILITY_LIMIT"
    APPROVAL_MISSING = "APPROVAL_MISSING"
    INSTRUMENT_NOT_ACCEPTED = "INSTRUMENT_NOT_ACCEPTED"
    SESSION_INELIGIBLE = "SESSION_INELIGIBLE"
    BUDGET_INSUFFICIENT = "BUDGET_INSUFFICIENT"
    COHORT_IDENTITY_INVALID = "COHORT_IDENTITY_INVALID"
    ESTIMATE_UNAVAILABLE = "ESTIMATE_UNAVAILABLE"
    ECONOMIC_RECEIPT_UNAVAILABLE = "ECONOMIC_RECEIPT_UNAVAILABLE"
    ECONOMIC_RECEIPT_MISMATCH = "ECONOMIC_RECEIPT_MISMATCH"
    QUOTE_PROVENANCE_MISMATCH = "QUOTE_PROVENANCE_MISMATCH"


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IST = ZoneInfo("Asia/Kolkata")


def _money(name: str, raw: Decimal | str | int) -> Decimal:
    try:
        value = raw if isinstance(raw, Decimal) else Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AdmissibilityError(f"{name} must be decimal") from exc
    if not value.is_finite():
        raise AdmissibilityError(f"{name} must be finite")
    return value


@dataclass(frozen=True, slots=True)
class TradeHurdle:
    """Versioned minimum net edge and uncertainty margin for one strategy."""

    strategy_id: str
    strategy_version: str
    rule_version: str
    minimum_net_pnl_inr: Decimal
    uncertainty_buffer_inr: Decimal
    minimum_portfolio_utility_delta_inr: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        for name in ("strategy_id", "strategy_version", "rule_version"):
            if not getattr(self, name).strip():
                raise AdmissibilityError(f"{name} is required")
        for name in ("minimum_net_pnl_inr", "uncertainty_buffer_inr",
                     "minimum_portfolio_utility_delta_inr"):
            value = _money(name, getattr(self, name))
            if name != "minimum_portfolio_utility_delta_inr" and value < 0:
                raise AdmissibilityError(f"{name} cannot be negative")
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class FrozenDecisionContext:
    """Decision-time eligibility and budget facts bound to a registered cohort."""

    cohort_id: str
    cohort_sha256: str
    session_date: date
    session_evidence_sha256: str
    session_eligible: bool
    accepted_instrument_ids: frozenset[str]
    code_sha256: str
    parameters_sha256: str
    model_sha256: str
    available_budget_inr: Decimal
    required_budget_inr: Decimal
    approval_granted: bool
    volatility_allowed: bool
    portfolio_utility_delta_inr: Decimal | None

    def __post_init__(self) -> None:
        if not self.cohort_id.strip():
            raise AdmissibilityError("cohort_id is required")
        for name in ("cohort_sha256", "session_evidence_sha256", "code_sha256",
                     "parameters_sha256", "model_sha256"):
            if not _SHA256.fullmatch(getattr(self, name)):
                raise AdmissibilityError(f"{name} must be lowercase SHA-256")
        if not self.accepted_instrument_ids or any(not x.strip() for x in self.accepted_instrument_ids):
            raise AdmissibilityError("accepted instrument set cannot be empty")
        for name in ("available_budget_inr", "required_budget_inr"):
            value = _money(name, getattr(self, name))
            if value < 0:
                raise AdmissibilityError(f"{name} cannot be negative")
            object.__setattr__(self, name, value)
        if self.portfolio_utility_delta_inr is not None:
            object.__setattr__(self, "portfolio_utility_delta_inr",
                               _money("portfolio_utility_delta_inr", self.portfolio_utility_delta_inr))
        for name in ("session_eligible", "approval_granted", "volatility_allowed"):
            if type(getattr(self, name)) is not bool:
                raise AdmissibilityError(f"{name} must be boolean")


@dataclass(frozen=True, slots=True)
class TradeDecision:
    state: DecisionState
    reasons: tuple[DecisionReason, ...]
    cohort_sha256: str | None
    expected_net_pnl_inr: Decimal | None
    uncertainty_buffer_inr: Decimal
    economic_receipt_sha256: str | None
    is_broker_authorization: bool = False


def _decision(state: DecisionState, reasons: set[DecisionReason], context: FrozenDecisionContext,
              estimate: NetEVEstimate | None, hurdle: TradeHurdle,
              economic_receipt: TradeEconomicsReceipt | None) -> TradeDecision:
    return TradeDecision(state, tuple(sorted(reasons, key=lambda x: x.value)), context.cohort_sha256,
                         estimate.expected_net_pnl_inr if estimate else None,
                         hurdle.uncertainty_buffer_inr,
                         economic_receipt.receipt_sha256 if economic_receipt else None)


def evaluate_trade_admissibility(
    *,
    signal: TraderSignal,
    estimate: NetEVEstimate | None,
    economic_receipt: TradeEconomicsReceipt | None,
    execution: ExecutionEvidence,
    hurdle: TradeHurdle,
    context: FrozenDecisionContext,
    freeze_registry: StrategyFreezeRegistry,
) -> TradeDecision:
    """Produce TRADE/WATCH/REJECT/CASH using only decision-time, frozen evidence.

    Missing evidence never defaults to permission. A candidate passes only when its
    registered strategy freeze, session, instrument, approvals, volatility, budget,
    portfolio utility, executable quote, costed net edge and calibrated lower bound pass.
    """
    if signal.side in {SignalSide.WATCH, SignalSide.NO_TRADE}:
        return _decision(DecisionState.CASH, {DecisionReason.NO_EDGE}, context, estimate, hurdle,
                         economic_receipt)
    if (signal.strategy_id, signal.strategy_version) != (hurdle.strategy_id, hurdle.strategy_version):
        raise AdmissibilityError("hurdle does not match signal strategy identity/version")
    if economic_receipt is not None and not isinstance(economic_receipt, TradeEconomicsReceipt):
        raise AdmissibilityError("economic_receipt must be a TradeEconomicsReceipt")

    reasons: set[DecisionReason] = set()
    try:
        registered_hash = freeze_registry.validate_run(
            cohort_id=context.cohort_id,
            session=context.session_date,
            strategy_id=signal.strategy_id,
            code_hash=context.code_sha256,
            parameters_hash=context.parameters_sha256,
            model_hash=context.model_sha256,
        )
        if registered_hash != context.cohort_sha256:
            raise ParameterFreezeError("cohort digest differs from registered immutable declaration")
    except (ParameterFreezeError, OSError, ValueError):
        reasons.add(DecisionReason.COHORT_IDENTITY_INVALID)

    if signal.strategy_hash != context.code_sha256:
        reasons.add(DecisionReason.COHORT_IDENTITY_INVALID)
    if signal.decision_at.astimezone(_IST).date() != context.session_date:
        reasons.add(DecisionReason.SESSION_INELIGIBLE)
    if signal.instrument_id not in context.accepted_instrument_ids:
        reasons.add(DecisionReason.INSTRUMENT_NOT_ACCEPTED)
    if not context.session_eligible:
        reasons.add(DecisionReason.SESSION_INELIGIBLE)
    if context.available_budget_inr < context.required_budget_inr:
        reasons.add(DecisionReason.BUDGET_INSUFFICIENT)
    if not context.approval_granted:
        reasons.add(DecisionReason.APPROVAL_MISSING)
    if not context.volatility_allowed:
        reasons.add(DecisionReason.VOLATILITY_LIMIT)
    if context.portfolio_utility_delta_inr is None:
        reasons.add(DecisionReason.PORTFOLIO_UTILITY_UNAVAILABLE)
    elif context.portfolio_utility_delta_inr < hurdle.minimum_portfolio_utility_delta_inr:
        reasons.add(DecisionReason.MARGINAL_PORTFOLIO_HARM)

    feasibility = assess_execution_feasibility(execution)
    if "quote_timestamp_stale" in feasibility.reasons or "quote_stale" in feasibility.reasons:
        reasons.add(DecisionReason.STALE_FEED)
    if "market_halted" in feasibility.reasons:
        reasons.add(DecisionReason.INSTRUMENT_HALTED)
    if feasibility.state is FeasibilityState.WATCH:
        reasons.add(DecisionReason.EXECUTION_NOT_FEASIBLE)
    elif feasibility.state is FeasibilityState.NO_TRADE:
        reasons.add(DecisionReason.EXECUTION_NOT_FEASIBLE)

    if estimate is None:
        reasons.add(DecisionReason.ESTIMATE_UNAVAILABLE)
    else:
        if (estimate.quote_source_id != execution.quote_source_id or
                estimate.quote_source_sha256 != execution.quote_source_hash):
            reasons.add(DecisionReason.QUOTE_PROVENANCE_MISMATCH)
        if estimate.expected_net_pnl_inr <= 0:
            reasons.add(DecisionReason.NO_EDGE)
        if estimate.expected_gross_pnl_inr <= estimate.expected_cost_inr:
            reasons.add(DecisionReason.COSTS_EXCEED_GROSS_EDGE)
        if estimate.expected_net_pnl_inr <= hurdle.minimum_net_pnl_inr:
            reasons.add(DecisionReason.NET_EDGE_BELOW_HURDLE)
        if estimate.expected_net_pnl_inr <= hurdle.uncertainty_buffer_inr:
            reasons.add(DecisionReason.NET_EDGE_BELOW_HURDLE)
        interval = estimate.mean_confidence_interval_95_inr
        if interval is None:
            reasons.add(DecisionReason.CONFIDENCE_UNAVAILABLE)
        elif interval[0] <= hurdle.uncertainty_buffer_inr:
            reasons.add(DecisionReason.CONFIDENCE_TOO_LOW)

    if economic_receipt is None:
        reasons.add(DecisionReason.ECONOMIC_RECEIPT_UNAVAILABLE)
    else:
        receipt_matches = (
            _SHA256.fullmatch(economic_receipt.receipt_sha256) is not None
            and
            economic_receipt.signal_id == signal.signal_id and
            economic_receipt.instrument_id == signal.instrument_id and
            economic_receipt.decision_at_utc == signal.decision_at.astimezone(timezone.utc).isoformat() and
            economic_receipt.quote_source_id == execution.quote_source_id and
            economic_receipt.quote_source_sha256 == execution.quote_source_hash and
            (estimate is None or
             economic_receipt.cost_schedule_sha256 == estimate.cost_schedule_sha256)
        )
        if not receipt_matches:
            reasons.add(DecisionReason.ECONOMIC_RECEIPT_MISMATCH)

    hard = {
        DecisionReason.NO_EDGE, DecisionReason.COSTS_EXCEED_GROSS_EDGE,
        DecisionReason.NET_EDGE_BELOW_HURDLE, DecisionReason.VOLATILITY_LIMIT,
        DecisionReason.APPROVAL_MISSING, DecisionReason.INSTRUMENT_NOT_ACCEPTED,
        DecisionReason.SESSION_INELIGIBLE, DecisionReason.BUDGET_INSUFFICIENT,
        DecisionReason.COHORT_IDENTITY_INVALID, DecisionReason.MARGINAL_PORTFOLIO_HARM,
        DecisionReason.INSTRUMENT_HALTED, DecisionReason.ECONOMIC_RECEIPT_MISMATCH,
    }
    if feasibility.state is FeasibilityState.NO_TRADE:
        return _decision(DecisionState.REJECT, reasons, context, estimate, hurdle, economic_receipt)
    if reasons & hard:
        # A utility-dominated opportunity explicitly leaves the capital unallocated.
        state = DecisionState.CASH if DecisionReason.MARGINAL_PORTFOLIO_HARM in reasons else DecisionState.REJECT
        return _decision(state, reasons, context, estimate, hurdle, economic_receipt)
    if reasons:
        return _decision(DecisionState.WATCH, reasons, context, estimate, hurdle, economic_receipt)
    return _decision(DecisionState.TRADE, set(), context, estimate, hurdle, economic_receipt)


__all__ = [
    "AdmissibilityError", "DecisionReason", "DecisionState", "FrozenDecisionContext",
    "TradeDecision", "TradeHurdle", "evaluate_trade_admissibility",
]
