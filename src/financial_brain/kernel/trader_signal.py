"""Canonical immutable signal envelope for research and paper-trading desks."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import hashlib
import json
from typing import Literal
from uuid import UUID


class SignalContractError(ValueError):
    """A canonical signal is malformed or temporally inconsistent."""


class DeskId(StrEnum):
    SWING = "SWING"
    INTRADAY = "INTRADAY"
    OPTIONS = "OPTIONS"
    ETF = "ETF"
    MUTUAL_FUND = "MUTUAL_FUND"


class SignalSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    SELL_SHORT = "SELL_SHORT"
    BUY_TO_COVER = "BUY_TO_COVER"
    WATCH = "WATCH"
    NO_TRADE = "NO_TRADE"


class EvidenceTier(StrEnum):
    FIXTURE = "FIXTURE"
    REPLAY = "REPLAY"
    LOCAL_PAPER = "LOCAL_PAPER"
    ATTESTED_FORWARD = "ATTESTED_FORWARD"
    BROKER_APPROVED = "BROKER_APPROVED"


class ExecutionIntentType(StrEnum):
    EXCHANGE_LIMIT = "EXCHANGE_LIMIT"
    EXCHANGE_MARKETABLE_SIMULATION = "EXCHANGE_MARKETABLE_SIMULATION"
    MF_SUBSCRIBE = "MF_SUBSCRIBE"
    MF_REDEEM = "MF_REDEEM"
    MF_SWITCH = "MF_SWITCH"
    MF_SIP = "MF_SIP"
    NO_TRADE = "NO_TRADE"


class EntryCondition(StrEnum):
    LIMIT_ZONE = "LIMIT_ZONE"
    BREAKOUT_CONFIRMATION = "BREAKOUT_CONFIRMATION"
    PULLBACK_CONFIRMATION = "PULLBACK_CONFIRMATION"
    MARKETABLE_QUOTE = "MARKETABLE_QUOTE"


class ExitTrigger(StrEnum):
    HARD_STOP = "HARD_STOP"
    TAKE_PROFIT = "TAKE_PROFIT"
    TRAILING_STOP = "TRAILING_STOP"
    SIGNAL_REVERSAL = "SIGNAL_REVERSAL"
    TIME_STOP = "TIME_STOP"
    SESSION_FLATTEN = "SESSION_FLATTEN"
    RISK_KILL_SWITCH = "RISK_KILL_SWITCH"
    MANUAL_CLOSE = "MANUAL_CLOSE"
    EXPIRY_OR_CORPORATE_ACTION = "EXPIRY_OR_CORPORATE_ACTION"


class PriceBasis(StrEnum):
    UNDERLYING = "UNDERLYING"
    ETF_MARKET_QUOTE = "ETF_MARKET_QUOTE"
    OPTION_EXECUTABLE_PREMIUM = "OPTION_EXECUTABLE_PREMIUM"
    OPTION_SPREAD_VALUE = "OPTION_SPREAD_VALUE"
    PUBLISHED_NAV_POLICY = "PUBLISHED_NAV_POLICY"


class SignalState(StrEnum):
    DETECTED = "DETECTED"
    ELIGIBLE = "ELIGIBLE"
    REJECTED = "REJECTED"
    APPROVED = "APPROVED"
    SHADOW = "SHADOW"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    MISSED = "MISSED"
    EXITED = "EXITED"
    EXPIRED = "EXPIRED"


_SIGNAL_TRANSITIONS = {
    SignalState.DETECTED: {SignalState.ELIGIBLE, SignalState.REJECTED},
    SignalState.ELIGIBLE: {SignalState.APPROVED, SignalState.SHADOW, SignalState.EXPIRED},
    SignalState.APPROVED: {SignalState.SUBMITTED, SignalState.EXPIRED},
    SignalState.SHADOW: {SignalState.SUBMITTED, SignalState.EXPIRED},
    SignalState.SUBMITTED: {SignalState.FILLED, SignalState.PARTIAL,
                            SignalState.MISSED, SignalState.EXPIRED},
    SignalState.PARTIAL: {SignalState.PARTIAL, SignalState.FILLED,
                          SignalState.MISSED, SignalState.EXPIRED},
    SignalState.FILLED: {SignalState.EXITED},
}


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SignalContractError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class TraderSignal:
    """Decision-time signal identity with source and strategy provenance."""

    signal_id: str
    desk_id: DeskId
    strategy_id: str
    strategy_version: str
    strategy_hash: str
    instrument_id: str
    side: SignalSide
    as_of: datetime
    decision_at: datetime
    expires_at: datetime
    evidence_tier: EvidenceTier
    source_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.desk_id, DeskId):
            raise SignalContractError("desk_id must be a DeskId")
        if not isinstance(self.side, SignalSide):
            raise SignalContractError("side must be a SignalSide")
        if not isinstance(self.evidence_tier, EvidenceTier):
            raise SignalContractError("evidence_tier must be an EvidenceTier")
        try:
            parsed = UUID(self.signal_id)
        except (ValueError, AttributeError) as exc:
            raise SignalContractError("signal_id must be a UUID") from exc
        if str(parsed) != self.signal_id.lower():
            raise SignalContractError("signal_id must use canonical UUID form")
        for name in ("strategy_id", "strategy_version", "instrument_id"):
            if not getattr(self, name).strip():
                raise SignalContractError(f"{name} is required")
        for name in ("strategy_hash", "source_hash"):
            if not _SHA256.fullmatch(getattr(self, name)):
                raise SignalContractError(f"{name} must be lowercase SHA-256")
        for name in ("as_of", "decision_at", "expires_at"):
            _aware(name, getattr(self, name))
        if self.as_of.astimezone(timezone.utc) > self.decision_at.astimezone(timezone.utc):
            raise SignalContractError("as_of cannot be later than decision_at")
        if self.expires_at.astimezone(timezone.utc) <= self.decision_at.astimezone(timezone.utc):
            raise SignalContractError("expires_at must be later than decision_at")


@dataclass(frozen=True, slots=True)
class DecisionFeature:
    name: str
    value: Decimal | str | bool
    observed_at: datetime
    source_hash: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise SignalContractError("feature name is required")
        _aware("feature observed_at", self.observed_at)
        if not _SHA256.fullmatch(self.source_hash):
            raise SignalContractError("feature source_hash must be lowercase SHA-256")
        if isinstance(self.value, Decimal) and not self.value.is_finite():
            raise SignalContractError("numeric feature values must be finite")


@dataclass(frozen=True, slots=True)
class ExpectedReturnDistribution:
    mean: Decimal
    lower_quantile: Decimal
    upper_quantile: Decimal
    confidence: Decimal
    horizon: str

    def __post_init__(self) -> None:
        for name in ("mean", "lower_quantile", "upper_quantile", "confidence"):
            try:
                value = Decimal(getattr(self, name))
            except (InvalidOperation, TypeError) as exc:
                raise SignalContractError(f"{name} must be decimal") from exc
            if not value.is_finite():
                raise SignalContractError(f"{name} must be finite")
            object.__setattr__(self, name, value)
        if self.lower_quantile > self.mean or self.mean > self.upper_quantile:
            raise SignalContractError("return quantiles must bracket the mean")
        if not Decimal(0) < self.confidence <= Decimal(1):
            raise SignalContractError("confidence must be in (0, 1]")
        if not self.horizon.strip():
            raise SignalContractError("distribution horizon is required")


@dataclass(frozen=True, slots=True)
class DecisionAssessment:
    signal_id: str
    decision_at: datetime
    features: tuple[DecisionFeature, ...]
    evidence: tuple[DecisionFeature, ...]
    expected_return: ExpectedReturnDistribution
    benchmark_id: str
    estimated_cost_bps: Decimal
    risk_budget_inr: Decimal
    expected_return_inr: Decimal
    contraindications: tuple[str, ...]

    def __post_init__(self) -> None:
        try:
            UUID(self.signal_id)
        except (ValueError, AttributeError) as exc:
            raise SignalContractError("assessment signal_id must be a UUID") from exc
        _aware("assessment decision_at", self.decision_at)
        if not self.benchmark_id.strip():
            raise SignalContractError("benchmark_id is required")
        if not self.features or not self.evidence:
            raise SignalContractError("decision-time features and evidence are required")
        if any(item.observed_at.astimezone(timezone.utc) > self.decision_at.astimezone(timezone.utc)
               for item in (*self.features, *self.evidence)):
            raise SignalContractError("assessment cannot include future features or evidence")
        for name in ("estimated_cost_bps", "risk_budget_inr", "expected_return_inr"):
            try:
                value = Decimal(getattr(self, name))
            except (InvalidOperation, TypeError) as exc:
                raise SignalContractError(f"{name} must be decimal") from exc
            if not value.is_finite() or (name != "expected_return_inr" and value < 0):
                raise SignalContractError(f"{name} is outside its valid range")
            object.__setattr__(self, name, value)
        if not self.contraindications or any(not reason.strip() for reason in self.contraindications):
            raise SignalContractError("contraindications must include reasons or explicit NONE")


@dataclass(frozen=True, slots=True)
class ExecutionIntent:
    """Typed research or paper intent; it cannot express a physical broker order."""

    intent_id: str
    signal_id: str
    intent_type: ExecutionIntentType
    instrument_id: str | None = None
    quantity: Decimal | None = None
    amount_inr: Decimal | None = None
    limit_price: Decimal | None = None
    quote_hash: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.intent_type, ExecutionIntentType):
            raise SignalContractError("intent_type must be an ExecutionIntentType")
        for name in ("intent_id", "signal_id"):
            try:
                UUID(getattr(self, name))
            except (ValueError, AttributeError) as exc:
                raise SignalContractError(f"{name} must be a UUID") from exc
        if self.intent_type is ExecutionIntentType.NO_TRADE:
            if not self.reason or not self.reason.strip():
                raise SignalContractError("NO_TRADE intent requires a reason")
            if any(value is not None for value in
                   (self.instrument_id, self.quantity, self.amount_inr,
                    self.limit_price, self.quote_hash)):
                raise SignalContractError("NO_TRADE cannot carry execution fields")
            return
        if not self.instrument_id or not self.instrument_id.strip():
            raise SignalContractError("instrument_id is required")
        if self.intent_type in {ExecutionIntentType.EXCHANGE_LIMIT,
                                ExecutionIntentType.EXCHANGE_MARKETABLE_SIMULATION}:
            if self.quantity is None or self.amount_inr is not None:
                raise SignalContractError("exchange intent requires quantity only")
            self._positive("quantity", self.quantity)
            if self.intent_type is ExecutionIntentType.EXCHANGE_LIMIT:
                if self.limit_price is None or self.quote_hash is not None:
                    raise SignalContractError("exchange limit requires limit_price, not quote_hash")
                self._positive("limit_price", self.limit_price)
            elif self.quote_hash is None or not _SHA256.fullmatch(self.quote_hash):
                raise SignalContractError("marketable simulation requires a quote SHA-256")
            if self.reason is not None:
                raise SignalContractError("exchange intent cannot carry an MF reason")
        else:
            if self.amount_inr is None or self.quantity is not None or self.limit_price is not None:
                raise SignalContractError("MF intent requires amount_inr, not exchange pricing fields")
            self._positive("amount_inr", self.amount_inr)
            if self.quote_hash is not None:
                raise SignalContractError("MF intent cannot carry an executable quote hash")

    @staticmethod
    def _positive(name: str, value: Decimal) -> None:
        try:
            number = Decimal(value)
        except (InvalidOperation, TypeError) as exc:
            raise SignalContractError(f"{name} must be decimal") from exc
        if not number.is_finite() or number <= 0:
            raise SignalContractError(f"{name} must be finite and positive")


@dataclass(frozen=True, slots=True)
class TradePlan:
    """Versionable paper-trade conditions and instrument precision constraints."""

    instrument_id: str
    side: Literal["BUY", "SELL_SHORT"]
    price_basis: PriceBasis
    entry_condition: EntryCondition
    entry_low: Decimal
    entry_high: Decimal
    stop_price: Decimal
    target_price: Decimal
    invalidation: str
    max_holding_sessions: int
    tick_size: Decimal
    lot_size: Decimal
    unit_precision: int
    quantity: Decimal
    entry_expires_at: datetime
    exit_triggers: tuple[ExitTrigger, ...]
    paper_execution_eligible: bool
    ineligibility_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.instrument_id.strip():
            raise SignalContractError("trade-plan instrument_id is required")
        if not isinstance(self.entry_condition, EntryCondition):
            raise SignalContractError("entry_condition must be an EntryCondition")
        if not isinstance(self.price_basis, PriceBasis):
            raise SignalContractError("price_basis must be a PriceBasis")
        if self.side not in {"BUY", "SELL_SHORT"}:
            raise SignalContractError("trade-plan side must be BUY or SELL_SHORT")
        for name in ("entry_low", "entry_high", "stop_price", "target_price", "tick_size",
                     "lot_size", "quantity"):
            value = getattr(self, name)
            try:
                number = Decimal(value)
            except (InvalidOperation, TypeError) as exc:
                raise SignalContractError(f"{name} must be decimal") from exc
            if not number.is_finite() or number <= 0:
                raise SignalContractError(f"{name} must be finite and positive")
            object.__setattr__(self, name, number)
        if self.entry_low > self.entry_high:
            raise SignalContractError("entry_low cannot exceed entry_high")
        if self.side == "BUY" and not (
            self.stop_price < self.entry_low <= self.entry_high < self.target_price
        ):
            raise SignalContractError("long stop and target must bracket the entry zone")
        if self.side == "SELL_SHORT" and not (
            self.target_price < self.entry_low <= self.entry_high < self.stop_price
        ):
            raise SignalContractError("short target and stop must bracket the entry zone")
        if not self.invalidation.strip():
            raise SignalContractError("strategy invalidation is required")
        if self.entry_expires_at.tzinfo is None or self.entry_expires_at.utcoffset() is None:
            raise SignalContractError("entry_expires_at must be timezone-aware")
        if not self.exit_triggers or any(not isinstance(trigger, ExitTrigger) for trigger in self.exit_triggers):
            raise SignalContractError("trade plan requires typed conditional exit triggers")
        if len(set(self.exit_triggers)) != len(self.exit_triggers):
            raise SignalContractError("exit triggers must be unique")
        if self.max_holding_sessions < 1:
            raise SignalContractError("max_holding_sessions must be positive")
        if self.unit_precision < 0 or self.unit_precision > 12:
            raise SignalContractError("unit_precision must be between 0 and 12")
        if not self.paper_execution_eligible and not (
            self.ineligibility_reason and self.ineligibility_reason.strip()
        ):
            raise SignalContractError("ineligible trade plan requires a reason")
        if self.paper_execution_eligible and self.ineligibility_reason is not None:
            raise SignalContractError("eligible trade plan cannot carry an ineligibility reason")


def validate_trade_plan_signal(signal: TraderSignal, plan: TradePlan) -> None:
    """Bind plan identity, side and expiry to the immutable decision signal."""
    if (signal.instrument_id != plan.instrument_id or signal.side.value != plan.side):
        raise SignalContractError("trade plan instrument/side differs from its signal")
    if signal.expires_at.astimezone(timezone.utc) != plan.entry_expires_at.astimezone(timezone.utc):
        raise SignalContractError("trade plan entry expiry differs from signal expiry")
    if plan.entry_expires_at.astimezone(timezone.utc) <= signal.decision_at.astimezone(timezone.utc):
        raise SignalContractError("trade plan expiry must be after the signal decision")


@dataclass(frozen=True, slots=True)
class SignalLifecycleEvent:
    state: SignalState
    occurred_at: datetime
    idempotency_key: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, SignalState):
            raise SignalContractError("lifecycle state must be a SignalState")
        _aware("lifecycle occurred_at", self.occurred_at)
        if not self.idempotency_key.strip():
            raise SignalContractError("lifecycle idempotency_key is required")
        if self.state in {SignalState.REJECTED, SignalState.MISSED, SignalState.EXPIRED} and not (
            self.reason and self.reason.strip()
        ):
            raise SignalContractError(f"{self.state.value} lifecycle event requires a reason")


@dataclass(frozen=True, slots=True)
class SignalLifecycle:
    """Append-only, event-time-ordered signal state with idempotent transitions."""

    signal_id: str
    events: tuple[SignalLifecycleEvent, ...]

    def __post_init__(self) -> None:
        try:
            UUID(self.signal_id)
        except (ValueError, AttributeError) as exc:
            raise SignalContractError("lifecycle signal_id must be a UUID") from exc
        if not self.events or self.events[0].state is not SignalState.DETECTED:
            raise SignalContractError("signal lifecycle must start at DETECTED")
        keys: set[str] = set()
        for index, event in enumerate(self.events):
            if event.idempotency_key in keys:
                raise SignalContractError("lifecycle idempotency keys must be unique")
            keys.add(event.idempotency_key)
            if index:
                previous = self.events[index - 1]
                if event.occurred_at.astimezone(timezone.utc) < previous.occurred_at.astimezone(timezone.utc):
                    raise SignalContractError("lifecycle event time cannot move backwards")
                if event.state not in _SIGNAL_TRANSITIONS.get(previous.state, set()):
                    raise SignalContractError(
                        f"invalid signal transition {previous.state.value} -> {event.state.value}"
                    )

    @property
    def state(self) -> SignalState:
        return self.events[-1].state

    @property
    def digest(self) -> str:
        body = [{"state": event.state.value,
                 "occurred_at": event.occurred_at.astimezone(timezone.utc).isoformat(),
                 "idempotency_key": event.idempotency_key,
                 "reason": event.reason} for event in self.events]
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def advance(self, event: SignalLifecycleEvent) -> SignalLifecycle:
        prior = next((existing for existing in self.events
                      if existing.idempotency_key == event.idempotency_key), None)
        if prior is not None:
            if prior != event:
                raise SignalContractError("idempotency key reused with different event")
            return self
        return SignalLifecycle(self.signal_id, (*self.events, event))


@dataclass(frozen=True, slots=True)
class AlertDeduplication:
    duplicate: bool
    reason: str
    price_move_bps: Decimal | None


def compare_alerts(
    prior_signal: TraderSignal,
    prior_plan: TradePlan,
    candidate_signal: TraderSignal,
    candidate_plan: TradePlan,
    *,
    deduplication_window: timedelta,
    material_price_move_bps: Decimal | str | int,
) -> AlertDeduplication:
    """Compare alerts without collapsing new versions or meaningful price changes."""
    if candidate_signal.decision_at.astimezone(timezone.utc) < prior_signal.decision_at.astimezone(timezone.utc):
        raise SignalContractError("alert event time cannot move backwards")
    if deduplication_window <= timedelta(0):
        raise SignalContractError("deduplication_window must be positive")
    try:
        threshold = Decimal(material_price_move_bps)
    except (InvalidOperation, TypeError) as exc:
        raise SignalContractError("material_price_move_bps must be decimal") from exc
    if not threshold.is_finite() or threshold < 0:
        raise SignalContractError("material_price_move_bps must be finite and non-negative")
    same_rule = (
        prior_signal.strategy_id == candidate_signal.strategy_id
        and prior_signal.strategy_version == candidate_signal.strategy_version
        and prior_signal.strategy_hash == candidate_signal.strategy_hash
    )
    same_target = (prior_signal.instrument_id == candidate_signal.instrument_id
                   and prior_signal.side == candidate_signal.side
                   and prior_plan.side == candidate_plan.side)
    if not same_rule or not same_target:
        return AlertDeduplication(False, "NEW_RULE_OR_TARGET", None)
    elapsed = (candidate_signal.decision_at.astimezone(timezone.utc)
               - prior_signal.decision_at.astimezone(timezone.utc))
    if elapsed > deduplication_window:
        return AlertDeduplication(False, "OUTSIDE_DEDUPLICATION_WINDOW", None)
    prior_mid = (prior_plan.entry_low + prior_plan.entry_high) / Decimal(2)
    candidate_mid = (candidate_plan.entry_low + candidate_plan.entry_high) / Decimal(2)
    move_bps = abs(candidate_mid - prior_mid) / prior_mid * Decimal(10_000)
    if move_bps > threshold:
        return AlertDeduplication(False, "MATERIAL_PRICE_CHANGE", move_bps)
    return AlertDeduplication(True, "REPEATED_ALERT", move_bps)


__all__ = [
    "AlertDeduplication", "DecisionAssessment", "DecisionFeature", "DeskId", "EvidenceTier",
    "EntryCondition", "ExecutionIntent", "ExecutionIntentType",
    "ExpectedReturnDistribution", "ExitTrigger", "PriceBasis", "SignalContractError", "SignalLifecycle",
    "SignalLifecycleEvent", "SignalSide", "SignalState", "TradePlan", "TraderSignal",
    "compare_alerts", "validate_trade_plan_signal",
]
