"""Fail-closed decision gate for whether a proposal is actionable now."""
from __future__ import annotations

from dataclasses import dataclass
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
