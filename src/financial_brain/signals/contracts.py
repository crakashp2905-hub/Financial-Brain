"""One immutable signal schema for all five desks (checklist D3, sections 4 and 4A).

A desk does not invent its own shape. Every signal carries the same identity, timing, evidence and
execution-intent fields. Exchange trades must also carry an executable entry, a stop, a target and an
exit policy *before* they can be paper-entered; a stop or target is a trigger, not a fill price.
Mutual-fund signals carry NAV-based decision thresholds instead of exchange stop orders.

Validation happens at construction, so an invalid or contradictory signal never reaches a ledger.
Money and prices are ``Decimal``. Timestamps must be timezone-aware.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class SignalError(ValueError):
    """A signal that breaks the contract. Raised before any ledger is touched."""


class Desk(str, Enum):
    SWING = "SWING"
    INTRADAY = "INTRADAY"
    OPTIONS = "OPTIONS"
    ETF = "ETF"
    MUTUAL_FUND = "MUTUAL_FUND"


class EvidenceTier(str, Enum):
    FIXTURE = "FIXTURE"
    REPLAY = "REPLAY"
    LOCAL_PAPER = "LOCAL_PAPER"
    ATTESTED_FORWARD = "ATTESTED_FORWARD"
    BROKER_APPROVED = "BROKER_APPROVED"


class Intent(str, Enum):
    """Execution intent (FB-0403)."""

    EXCHANGE_LIMIT = "EXCHANGE_LIMIT"
    EXCHANGE_MARKETABLE_SIMULATION = "EXCHANGE_MARKETABLE_SIMULATION"
    MF_SUBSCRIBE = "MF_SUBSCRIBE"
    MF_REDEEM = "MF_REDEEM"
    MF_SWITCH = "MF_SWITCH"
    MF_SIP = "MF_SIP"
    NO_TRADE = "NO_TRADE"


EXCHANGE_INTENTS = frozenset({Intent.EXCHANGE_LIMIT, Intent.EXCHANGE_MARKETABLE_SIMULATION})
MF_INTENTS = frozenset({Intent.MF_SUBSCRIBE, Intent.MF_REDEEM, Intent.MF_SWITCH, Intent.MF_SIP})
EXCHANGE_DESKS = frozenset({Desk.SWING, Desk.INTRADAY, Desk.OPTIONS, Desk.ETF})


class Side(str, Enum):
    BUY = "BUY"
    SELL_SHORT = "SELL_SHORT"   # permitted intraday shorts only (FB-0452)


class ExitTrigger(str, Enum):
    """Ordered exit triggers (FB-0454)."""

    HARD_STOP = "HARD_STOP"
    TAKE_PROFIT = "TAKE_PROFIT"
    TRAILING_STOP = "TRAILING_STOP"
    SIGNAL_REVERSAL = "SIGNAL_REVERSAL"
    TIME_STOP = "TIME_STOP"
    SESSION_FLATTEN = "SESSION_FLATTEN"
    RISK_KILL_SWITCH = "RISK_KILL_SWITCH"
    MANUAL_CLOSE = "MANUAL_CLOSE"
    EXPIRY_OR_CORPORATE_ACTION = "EXPIRY_OR_CORPORATE_ACTION"


class Lifecycle(str, Enum):
    """Signal lifecycle (FB-0405)."""

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


_NEXT: dict[Lifecycle, frozenset[Lifecycle]] = {
    Lifecycle.DETECTED: frozenset({Lifecycle.ELIGIBLE, Lifecycle.REJECTED}),
    Lifecycle.ELIGIBLE: frozenset({Lifecycle.APPROVED, Lifecycle.SHADOW, Lifecycle.EXPIRED}),
    Lifecycle.APPROVED: frozenset({Lifecycle.SUBMITTED, Lifecycle.EXPIRED}),
    Lifecycle.SHADOW: frozenset({Lifecycle.EXPIRED}),
    Lifecycle.SUBMITTED: frozenset({Lifecycle.FILLED, Lifecycle.PARTIAL, Lifecycle.MISSED}),
    Lifecycle.PARTIAL: frozenset({Lifecycle.FILLED, Lifecycle.EXITED, Lifecycle.MISSED}),
    Lifecycle.FILLED: frozenset({Lifecycle.EXITED}),
    Lifecycle.MISSED: frozenset(),
    Lifecycle.REJECTED: frozenset(),
    Lifecycle.EXITED: frozenset(),
    Lifecycle.EXPIRED: frozenset(),
}


def advance(state: Lifecycle, to: Lifecycle) -> Lifecycle:
    """Move a signal to its next state, refusing any transition the lifecycle does not allow."""
    if to not in _NEXT[state]:
        raise SignalError(f"illegal lifecycle step {state.value} -> {to.value}")
    return to


def _aware(name: str, ts: datetime) -> None:
    if not isinstance(ts, datetime) or ts.tzinfo is None or ts.utcoffset() is None:
        raise SignalError(f"{name} must be a timezone-aware datetime")


def _dec(name: str, value: object, *, allow_zero: bool = False) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, str)):
        raise SignalError(f"{name} must be a Decimal, got {type(value).__name__}")
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    if not d.is_finite() or d < 0 or (d == 0 and not allow_zero):
        raise SignalError(f"{name} must be a positive finite decimal, got {value!r}")
    return d


def on_tick(price: Decimal, tick: Decimal) -> bool:
    """Whether a price is an exact multiple of the instrument's tick (FB-0461)."""
    if tick <= 0:
        raise SignalError("tick must be positive")
    return price % tick == 0


@dataclass(frozen=True)
class ExitPolicy:
    """Ordered exit triggers, precommitted at entry (FB-0452, FB-0454)."""

    triggers: tuple[ExitTrigger, ...]
    version: str
    #: A versioned, explicitly approved reason to run without a hard stop or time stop (FB-0459).
    exception: str | None = None

    def __post_init__(self) -> None:
        if not self.triggers:
            raise SignalError("an exit policy needs at least one trigger")
        if len(set(self.triggers)) != len(self.triggers):
            raise SignalError("exit triggers must not repeat")
        if not self.version:
            raise SignalError("exit policy needs a version")
        if ExitTrigger.HARD_STOP not in self.triggers and not self.exception:
            raise SignalError("exit policy without HARD_STOP needs a versioned exception")


@dataclass(frozen=True)
class Signal:
    """One desk-agnostic signal. Construct it; ``__post_init__`` enforces the contract."""

    signal_id: str
    desk: Desk
    strategy_id: str
    strategy_version: str
    strategy_hash: str
    instrument_id: str
    intent: Intent
    as_of: datetime
    decision_at: datetime
    expires_at: datetime
    evidence_tier: EvidenceTier
    source_hash: str
    side: Side | None = None
    quantity: Decimal | None = None
    entry_limit: Decimal | None = None
    stop_price: Decimal | None = None
    target_price: Decimal | None = None
    tick_size: Decimal | None = None
    exit_policy: ExitPolicy | None = None
    #: Text reason; required for NO_TRADE so that a refusal is itself explained (FB-0462).
    reason: str | None = None
    #: Gross and net reward:risk floors this strategy requires (FB-0458); None means no gate.
    min_reward_risk: Decimal | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for label in ("signal_id", "strategy_id", "strategy_version", "strategy_hash",
                      "instrument_id", "source_hash"):
            if not getattr(self, label):
                raise SignalError(f"{label} is required")
        _aware("as_of", self.as_of)
        _aware("decision_at", self.decision_at)
        _aware("expires_at", self.expires_at)
        if self.decision_at < self.as_of:
            raise SignalError("decision_at cannot precede as_of")
        if self.expires_at <= self.decision_at:
            raise SignalError("expires_at must be after decision_at")
        for name in ("entry_limit", "stop_price", "target_price", "tick_size"):
            object.__setattr__(self, name, _dec(name, getattr(self, name)))
        object.__setattr__(self, "quantity", _dec("quantity", self.quantity))
        object.__setattr__(self, "min_reward_risk", _dec("min_reward_risk", self.min_reward_risk))

        if self.intent is Intent.NO_TRADE:
            self._check_no_trade()
        elif self.intent in EXCHANGE_INTENTS:
            self._check_exchange()
        else:
            self._check_mf()

    # ----------------------------------------------------------------- per-intent rules

    def _check_no_trade(self) -> None:
        if not self.reason:
            raise SignalError("NO_TRADE must state its reason")
        if self.side is not None or self.quantity is not None or self.stop_price or self.target_price:
            raise SignalError("NO_TRADE cannot carry a side, size or protection")

    def _check_exchange(self) -> None:
        if self.desk not in EXCHANGE_DESKS:
            raise SignalError(f"{self.desk.value} desk cannot use exchange intent {self.intent.value}")
        if self.side is None:
            raise SignalError("an exchange trade needs a side")
        if self.side is Side.SELL_SHORT and self.desk is Desk.SWING:
            raise SignalError("overnight equity shorts are not supported (FB-0461)")
        if self.quantity is None:
            raise SignalError("an exchange trade needs a quantity")
        if self.entry_limit is None:
            raise SignalError("an exchange trade needs an executable entry limit")
        if self.stop_price is None or self.target_price is None:
            raise SignalError("an exchange trade needs both a stop and a target before entry (FB-0452)")
        if self.exit_policy is None:
            raise SignalError("an exchange trade needs an exit policy before entry (FB-0472)")
        if self.desk is Desk.INTRADAY and ExitTrigger.SESSION_FLATTEN not in self.exit_policy.triggers:
            raise SignalError("intraday exits must include SESSION_FLATTEN (FB-0474)")
        if self.tick_size is None:
            raise SignalError("an exchange trade needs its tick size to check price rounding")
        for name in ("entry_limit", "stop_price", "target_price"):
            if not on_tick(getattr(self, name), self.tick_size):
                raise SignalError(f"{name} {getattr(self, name)} is not on tick {self.tick_size}")
        self._check_price_order()

    def _check_price_order(self) -> None:
        e, s, t = self.entry_limit, self.stop_price, self.target_price
        if e is None or s is None or t is None:
            raise SignalError("price ordering needs entry, stop and target")
        if self.side is Side.BUY:
            if not (s < e < t):
                raise SignalError("long plan needs stop < entry < target (FB-0459)")
        else:
            if not (t < e < s):
                raise SignalError("short plan needs target < entry < stop (FB-0459)")

    def _check_mf(self) -> None:
        if self.desk is not Desk.MUTUAL_FUND:
            raise SignalError(f"{self.desk.value} desk cannot use mutual-fund intent {self.intent.value}")
        if self.stop_price is not None or self.target_price is not None:
            raise SignalError("MF protection is a NAV decision threshold, not an exchange stop (FB-0481)")
        if self.side is not None:
            raise SignalError("MF signals are actions, not exchange sides")
        if self.quantity is not None and self.quantity <= 0:
            raise SignalError("MF quantity must be positive")

    # ----------------------------------------------------------------- economics

    def planned_loss(self) -> Decimal:
        """Planned loss to the stop, per unit times quantity. A plan, not a guaranteed bound (FB-0457)."""
        self._need_exchange_plan()
        if self.side is Side.BUY:
            return (self.entry_limit - self.stop_price) * self.quantity  # type: ignore[operator]
        return (self.stop_price - self.entry_limit) * self.quantity  # type: ignore[operator]

    def gross_reward_risk(self) -> Decimal:
        self._need_exchange_plan()
        if self.side is Side.BUY:
            reward = self.target_price - self.entry_limit  # type: ignore[operator]
            risk = self.entry_limit - self.stop_price  # type: ignore[operator]
        else:
            reward = self.entry_limit - self.target_price  # type: ignore[operator]
            risk = self.stop_price - self.entry_limit  # type: ignore[operator]
        return reward / risk

    def net_reward_risk(self, cost_per_unit: Decimal) -> Decimal:
        """Reward:risk after a per-unit round-trip cost (spread, fees, impact, taxes where modelled)."""
        if cost_per_unit < 0:
            raise SignalError("cost cannot be negative")
        self._need_exchange_plan()
        if self.side is Side.BUY:
            reward = self.target_price - self.entry_limit - cost_per_unit  # type: ignore[operator]
            risk = self.entry_limit - self.stop_price + cost_per_unit  # type: ignore[operator]
        else:
            reward = self.entry_limit - self.target_price - cost_per_unit  # type: ignore[operator]
            risk = self.stop_price - self.entry_limit + cost_per_unit  # type: ignore[operator]
        return reward / risk

    def passes_reward_risk_gate(self, cost_per_unit: Decimal) -> bool:
        if self.min_reward_risk is None:
            return True
        return self.net_reward_risk(cost_per_unit) >= self.min_reward_risk

    def _need_exchange_plan(self) -> None:
        if self.intent not in EXCHANGE_INTENTS or self.entry_limit is None or self.stop_price is None \
                or self.target_price is None or self.side is None or self.quantity is None:
            raise SignalError("economics need a complete exchange plan")
