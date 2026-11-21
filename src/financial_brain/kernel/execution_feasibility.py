"""Fail-closed decision gate for whether a proposal is actionable now."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum


class FeasibilityError(ValueError):
    """Invalid quote-quality or execution-feasibility input."""


class FeasibilityState(StrEnum):
    ACTIONABLE = "ACTIONABLE"
    WATCH = "WATCH"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True, slots=True)
class ExecutionEvidence:
    """Current quote evidence supplied to a decision gate; it is not an order."""

    bid: Decimal | None
    ask: Decimal | None
    quote_age_ms: int | None
    max_quote_age_ms: int
    spread_bps: Decimal | None
    max_spread_bps: Decimal
    estimated_slippage_bps: Decimal | None
    max_slippage_bps: Decimal
    visible_quantity: Decimal | None
    required_quantity: Decimal
    market_halted: bool = False
    option_spread_bps: Decimal | None = None
    max_option_spread_bps: Decimal | None = None
    redemption_allowed: bool | None = None
    quote_source_id: str | None = None
    quote_source_hash: str | None = None
    quote_event_at: datetime | None = None
    quote_received_at: datetime | None = None
    observed_at: datetime | None = None
    ltp_only: bool = False

    def __post_init__(self) -> None:
        if self.max_quote_age_ms <= 0 or (self.quote_age_ms is not None and self.quote_age_ms < 0):
            raise FeasibilityError("quote-age bounds must be positive and observed age nonnegative")
        for name in (
            "bid", "ask", "spread_bps", "max_spread_bps", "estimated_slippage_bps",
            "max_slippage_bps", "visible_quantity", "required_quantity", "option_spread_bps",
            "max_option_spread_bps",
        ):
            value = getattr(self, name)
            if value is None:
                continue
            try:
                number = Decimal(value)
            except (InvalidOperation, TypeError) as exc:
                raise FeasibilityError(f"{name} must be decimal") from exc
            if not number.is_finite() or number < 0:
                raise FeasibilityError(f"{name} must be finite and nonnegative")
            object.__setattr__(self, name, number)
        if self.required_quantity <= 0 or self.max_spread_bps <= 0 or self.max_slippage_bps <= 0:
            raise FeasibilityError("required quantity and spread/slippage limits must be positive")
        if (self.max_option_spread_bps is None) != (self.option_spread_bps is None):
            raise FeasibilityError("option spread and its limit must be supplied together")
        if self.max_option_spread_bps == 0:
            raise FeasibilityError("max_option_spread_bps must be positive")
        for name in ("quote_event_at", "quote_received_at", "observed_at"):
            value = getattr(self, name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise FeasibilityError(f"{name} must be timezone-aware")
        if self.quote_source_hash is not None and (
            len(self.quote_source_hash) != 64
            or any(char not in "0123456789abcdef" for char in self.quote_source_hash)
        ):
            raise FeasibilityError("quote_source_hash must be lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class FeasibilityDecision:
    state: FeasibilityState
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.state is FeasibilityState.ACTIONABLE and self.reasons:
            raise FeasibilityError("actionable decision cannot contain blocking reasons")
        if self.state is not FeasibilityState.ACTIONABLE and not self.reasons:
            raise FeasibilityError("watch/no-trade decision requires explicit reasons")


def assess_execution_feasibility(evidence: ExecutionEvidence) -> FeasibilityDecision:
    """Return ACTIONABLE only when quote, liquidity, slippage, and redemption checks pass."""
    hard: list[str] = []
    watch: list[str] = []
    if evidence.market_halted:
        hard.append("market_halted")
    if evidence.ltp_only:
        watch.append("ltp_only_non_executable")
    if not evidence.quote_source_id or not evidence.quote_source_hash:
        watch.append("quote_source_provenance_missing")
    clocks = (evidence.quote_event_at, evidence.quote_received_at, evidence.observed_at)
    if any(clock is None for clock in clocks):
        watch.append("quote_event_receipt_or_observation_clock_missing")
    else:
        event_at, received_at, observed_at = clocks
        assert event_at is not None and received_at is not None and observed_at is not None
        if received_at.astimezone(timezone.utc) < event_at.astimezone(timezone.utc):
            watch.append("quote_receipt_precedes_event")
        age_ms = (observed_at.astimezone(timezone.utc) - received_at.astimezone(timezone.utc)).total_seconds() * 1000
        if age_ms < 0:
            watch.append("quote_received_in_future")
        elif age_ms > evidence.max_quote_age_ms:
            watch.append("quote_timestamp_stale")
        if evidence.quote_age_ms is not None and age_ms >= 0 and abs(age_ms - evidence.quote_age_ms) > 1:
            watch.append("quote_age_mismatch")
    if evidence.redemption_allowed is False:
        hard.append("fund_redemption_blocked")
    if evidence.bid is None or evidence.ask is None or evidence.bid <= 0 or evidence.ask < evidence.bid:
        watch.append("no_viable_two_sided_quote")
    if evidence.quote_age_ms is None:
        watch.append("quote_age_unknown")
    elif evidence.quote_age_ms > evidence.max_quote_age_ms:
        watch.append("quote_stale")
    if evidence.spread_bps is None:
        watch.append("spread_unknown")
    elif evidence.spread_bps > evidence.max_spread_bps:
        hard.append("spread_exceeds_limit")
    if evidence.estimated_slippage_bps is None:
        watch.append("slippage_unknown")
    elif evidence.estimated_slippage_bps > evidence.max_slippage_bps:
        hard.append("slippage_exceeds_limit")
    if evidence.visible_quantity is None:
        watch.append("visible_depth_unknown")
    elif evidence.visible_quantity < evidence.required_quantity:
        hard.append("insufficient_visible_depth")
    if evidence.option_spread_bps is not None:
        if evidence.max_option_spread_bps is None:
            watch.append("option_spread_limit_unknown")
        elif evidence.option_spread_bps > evidence.max_option_spread_bps:
            hard.append("option_spread_exceeds_limit")
    if hard:
        return FeasibilityDecision(FeasibilityState.NO_TRADE, tuple(sorted(set(hard + watch))))
    if watch:
        return FeasibilityDecision(FeasibilityState.WATCH, tuple(sorted(set(watch))))
    return FeasibilityDecision(FeasibilityState.ACTIONABLE, ())


__all__ = ["ExecutionEvidence", "FeasibilityDecision", "FeasibilityError", "FeasibilityState", "assess_execution_feasibility"]
