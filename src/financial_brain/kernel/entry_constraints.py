"""Decision-time entry-price, slippage, spread and signal-TTL constraints."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from .execution_feasibility import ExecutionEvidence, FeasibilityState, assess_execution_feasibility
from .trader_signal import SignalSide, TradePlan, TraderSignal, validate_trade_plan_signal


class EntryConstraintError(ValueError):
    """Invalid price constraint or decision-time input."""


class EntryConstraintState(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    WATCH = "WATCH"
    REJECT = "REJECT"
    EXPIRED = "EXPIRED"


class EntryConstraintReason(StrEnum):
    SIGNAL_NOT_ACTIVE = "SIGNAL_NOT_ACTIVE"
    SIGNAL_EXPIRED_REQUIRES_REEVALUATION = "SIGNAL_EXPIRED_REQUIRES_REEVALUATION"
    SIGNAL_TTL_EXCEEDS_POLICY = "SIGNAL_TTL_EXCEEDS_POLICY"
    ENTRY_ZONE_BREACHED = "ENTRY_ZONE_BREACHED"
    MAX_SPREAD_EXCEEDED = "MAX_SPREAD_EXCEEDED"
    MAX_GAP_EXCEEDED = "MAX_GAP_EXCEEDED"
    MAX_SLIPPAGE_EXCEEDED = "MAX_SLIPPAGE_EXCEEDED"
    STALE_OR_INCOMPLETE_QUOTE = "STALE_OR_INCOMPLETE_QUOTE"
    EXECUTION_BLOCKED = "EXECUTION_BLOCKED"


def _decimal(name: str, raw: Decimal | str | int) -> Decimal:
    try:
        value = raw if isinstance(raw, Decimal) else Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise EntryConstraintError(f"{name} must be decimal") from exc
    if not value.is_finite() or value < 0:
        raise EntryConstraintError(f"{name} must be finite and non-negative")
    return value


@dataclass(frozen=True, slots=True)
class EntryConstraintPolicy:
    policy_id: str
    version: str
    max_signal_ttl_seconds: int
    max_spread_bps: Decimal
    max_gap_bps: Decimal
    max_slippage_bps: Decimal

    def __post_init__(self) -> None:
        if not self.policy_id.strip() or not self.version.strip():
            raise EntryConstraintError("policy identity and version are required")
        if type(self.max_signal_ttl_seconds) is not int or self.max_signal_ttl_seconds <= 0:
            raise EntryConstraintError("max_signal_ttl_seconds must be a positive integer")
        for name in ("max_spread_bps", "max_gap_bps", "max_slippage_bps"):
            value = _decimal(name, getattr(self, name))
            if value <= 0:
                raise EntryConstraintError(f"{name} must be positive")
            object.__setattr__(self, name, value)

    def digest(self) -> str:
        body = {"policy_id": self.policy_id, "version": self.version,
                "max_signal_ttl_seconds": self.max_signal_ttl_seconds,
                "max_spread_bps": str(self.max_spread_bps), "max_gap_bps": str(self.max_gap_bps),
                "max_slippage_bps": str(self.max_slippage_bps)}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class EntryConstraintDecision:
    state: EntryConstraintState
    reasons: tuple[EntryConstraintReason, ...]
    signal_id: str
    policy_sha256: str
    executable_entry_price: Decimal | None
    adverse_gap_bps: Decimal | None
    evaluated_at: datetime
    receipt_sha256: str


def evaluate_entry_constraints(*, signal: TraderSignal, plan: TradePlan,
                               policy: EntryConstraintPolicy,
                               execution: ExecutionEvidence,
                               decision_reference_price: Decimal | str | int,
                               evaluated_at: datetime) -> EntryConstraintDecision:
    """Check whether a current quote may satisfy a frozen plan before its TTL expires.

    Expired signals produce EXPIRED and require a newly evaluated signal; they can never
    generate a late paper fill. The returned evidence is a hash-pinned research/paper
    receipt only and does not submit an order.
    """
    validate_trade_plan_signal(signal, plan)
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise EntryConstraintError("evaluated_at must be timezone-aware")
    reference = _decimal("decision_reference_price", decision_reference_price)
    if reference <= 0:
        raise EntryConstraintError("decision_reference_price must be positive")
    now = evaluated_at.astimezone(timezone.utc)
    decision = signal.decision_at.astimezone(timezone.utc)
    expiry = min(signal.expires_at.astimezone(timezone.utc), plan.entry_expires_at.astimezone(timezone.utc))
    reasons: set[EntryConstraintReason] = set()
    state = EntryConstraintState.ELIGIBLE
    price: Decimal | None = None
    gap = Decimal(0)
    ttl_seconds = (expiry - decision).total_seconds()
    if ttl_seconds > policy.max_signal_ttl_seconds:
        reasons.add(EntryConstraintReason.SIGNAL_TTL_EXCEEDS_POLICY)
    if now < decision:
        state = EntryConstraintState.WATCH
        reasons.add(EntryConstraintReason.SIGNAL_NOT_ACTIVE)
    elif now >= expiry:
        state = EntryConstraintState.EXPIRED
        reasons.add(EntryConstraintReason.SIGNAL_EXPIRED_REQUIRES_REEVALUATION)
    else:
        price = execution.ask if signal.side is SignalSide.BUY else execution.bid
        if price is None or price <= 0:
            state = EntryConstraintState.WATCH
            reasons.add(EntryConstraintReason.STALE_OR_INCOMPLETE_QUOTE)
        else:
            lower, upper = plan.entry_low, plan.entry_high
            if not lower <= price <= upper:
                state = EntryConstraintState.REJECT
                reasons.add(EntryConstraintReason.ENTRY_ZONE_BREACHED)
            adverse = max(Decimal(0), price - reference) if signal.side is SignalSide.BUY else max(Decimal(0), reference - price)
            gap = adverse / reference * Decimal(10_000)
            if gap > policy.max_gap_bps:
                state = EntryConstraintState.REJECT
                reasons.add(EntryConstraintReason.MAX_GAP_EXCEEDED)
            if execution.spread_bps is None:
                state = EntryConstraintState.WATCH
                reasons.add(EntryConstraintReason.STALE_OR_INCOMPLETE_QUOTE)
            elif execution.spread_bps > policy.max_spread_bps:
                state = EntryConstraintState.REJECT
                reasons.add(EntryConstraintReason.MAX_SPREAD_EXCEEDED)
            if execution.estimated_slippage_bps is None:
                state = EntryConstraintState.WATCH
                reasons.add(EntryConstraintReason.STALE_OR_INCOMPLETE_QUOTE)
            elif execution.estimated_slippage_bps > policy.max_slippage_bps:
                state = EntryConstraintState.REJECT
                reasons.add(EntryConstraintReason.MAX_SLIPPAGE_EXCEEDED)

    feasibility = assess_execution_feasibility(execution)
    observed = execution.observed_at.astimezone(timezone.utc) if execution.observed_at else None
    if (observed is None or observed > now or
            (now - observed).total_seconds() * 1000 > execution.max_quote_age_ms):
        if state is EntryConstraintState.ELIGIBLE:
            state = EntryConstraintState.WATCH
        reasons.add(EntryConstraintReason.STALE_OR_INCOMPLETE_QUOTE)
    if feasibility.state is FeasibilityState.WATCH:
        if state is EntryConstraintState.ELIGIBLE:
            state = EntryConstraintState.WATCH
        reasons.add(EntryConstraintReason.STALE_OR_INCOMPLETE_QUOTE)
    elif feasibility.state is FeasibilityState.NO_TRADE:
        state = EntryConstraintState.REJECT
        reasons.add(EntryConstraintReason.EXECUTION_BLOCKED)
    if EntryConstraintReason.SIGNAL_TTL_EXCEEDS_POLICY in reasons and state is EntryConstraintState.ELIGIBLE:
        state = EntryConstraintState.REJECT

    reason_values = tuple(sorted(reasons, key=lambda item: item.value))
    policy_hash = policy.digest()
    body = {"state": state.value, "reasons": [item.value for item in reason_values],
            "signal_id": signal.signal_id, "strategy_hash": signal.strategy_hash,
            "instrument_id": signal.instrument_id, "plan": {
                "entry_condition": plan.entry_condition.value, "entry_low": str(plan.entry_low),
                "entry_high": str(plan.entry_high), "entry_expiry": expiry.isoformat()},
            "policy_sha256": policy_hash, "reference_price": str(reference),
            "executable_entry_price": str(price) if price is not None else None,
            "adverse_gap_bps": str(gap) if price is not None else None,
            "quote_source_id": execution.quote_source_id,
            "quote_source_hash": execution.quote_source_hash,
            "quote_event_at": execution.quote_event_at.isoformat() if execution.quote_event_at else None,
            "quote_received_at": execution.quote_received_at.isoformat() if execution.quote_received_at else None,
            "evaluated_at": now.isoformat()}
    receipt_hash = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return EntryConstraintDecision(state, reason_values, signal.signal_id, policy_hash, price, gap,
                                   now, receipt_hash)


__all__ = ["EntryConstraintDecision", "EntryConstraintError", "EntryConstraintPolicy",
           "EntryConstraintReason", "EntryConstraintState", "evaluate_entry_constraints"]
