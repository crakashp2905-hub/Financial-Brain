"""Algorithm specifications, clocks, orders, costs and abstention (ALG-012..022).

A spec is frozen, versioned and hashed. Validation fails closed: unknown fields, unsupported segments, short
selling in cash, and fixed US, London or Asian session clocks all raise. Behaviour adaptations are labelled as such;
an "exact parity" claim requires an authorised reference run, which this package never has.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from financial_brain.patterns.spec import canonical_json, sha256_hex

MARKET = "INDIA_NSE"
SEGMENTS = frozenset({"CASH_EQUITY", "INDEX_FUTURES", "STOCK_FUTURES"})
TIMEFRAMES = frozenset({"1m", "5m", "15m", "1h", "1D", "1W"})
FORBIDDEN_CLOCK_KEYS = frozenset({"london", "asian", "asia", "new_york", "et_open", "gmt"})
PARITY_LABEL = "BEHAVIOUR_ADAPTATION"  # ALG-018: the only parity label available without an authorised reference run


class SpecError(ValueError):
    """A specification, order or cost model is invalid."""


class FillClock(str, Enum):
    NEXT_OPEN = "NEXT_OPEN"
    NEXT_EVENT = "NEXT_EVENT"


@dataclass(frozen=True)
class BarClock:
    """Distinguishes the session, the chart bar, the signal close and the earliest eligible fill (ALG-013)."""

    session: str
    signal_bar: int
    fill: FillClock = FillClock.NEXT_OPEN

    def earliest_fill_index(self) -> int:
        """A signal on a closed bar can never fill at that bar's own open or earlier."""
        return self.signal_bar + 1


@dataclass(frozen=True)
class CostModel:
    """Versioned costs by segment (ALG-016). Every result must carry ``version``."""

    version: str
    segment: str
    fee_bps: float
    spread_bps: float
    impact_bps: float
    stt_or_tax_bps: float
    margin_fraction: float | None = None
    restrictions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.version or self.segment not in SEGMENTS:
            raise SpecError("cost model needs a version and a known segment")
        for name in ("fee_bps", "spread_bps", "impact_bps", "stt_or_tax_bps"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise SpecError(f"{name} must be finite and non-negative")

    def round_trip_bps(self) -> float:
        return 2 * (self.fee_bps + self.spread_bps / 2 + self.impact_bps) + self.stt_or_tax_bps

    def digest(self) -> str:
        return sha256_hex(canonical_json(self.__dict__))


def attach_cost_version(result: dict[str, Any], cost: CostModel) -> dict[str, Any]:
    """Refuses any execution result that does not name its cost model version (ALG-016)."""
    if not result:
        raise SpecError("empty result")
    out = dict(result)
    out["cost_model_version"] = cost.version
    out["cost_model_sha256"] = cost.digest()
    return out


@dataclass(frozen=True)
class OrderSpec:
    """Deterministic order definition (ALG-014)."""

    kind: str  # STOP, LIMIT, MARKET
    side: str  # BUY, SELL
    quantity: int
    tif: str = "DAY"  # DAY or IOC
    stop_price: float | None = None
    limit_price: float | None = None
    expiry_bars: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"STOP", "LIMIT", "MARKET"} or self.side not in {"BUY", "SELL"}:
            raise SpecError("unknown order kind or side")
        if self.quantity < 1:
            raise SpecError("quantity must be at least one unit")
        if self.tif not in {"DAY", "IOC"}:
            raise SpecError("unsupported time in force")
        if self.kind == "MARKET" and (self.stop_price is not None or self.limit_price is not None):
            raise SpecError("a market order cannot carry a price")
        if self.kind == "STOP" and self.stop_price is None:
            raise SpecError("a stop order needs a stop price")
        if self.kind == "LIMIT" and self.limit_price is None:
            raise SpecError("a limit order needs a limit price")
        if self.expiry_bars is not None and self.expiry_bars < 1:
            raise SpecError("expiry must be at least one bar")


@dataclass(frozen=True)
class AlgorithmSpec:
    """Versioned algorithm contract (ALG-012, ALG-015, ALG-019)."""

    algorithm_id: str
    source_ids: tuple[str, ...]
    family: str
    version: str
    segment: str
    timeframe: str
    clock: BarClock
    parameters: dict[str, Any] = field(default_factory=dict)
    long_allowed: bool = True
    short_allowed: bool = False
    cost_model_version: str = ""
    evidence_tier: str = "DESCRIPTIVE"
    market: str = MARKET
    parity: str = PARITY_LABEL

    def __post_init__(self) -> None:
        if not self.algorithm_id or not self.version:
            raise SpecError("algorithm_id and version are required")
        if not self.source_ids and self.family != "baseline":
            raise SpecError("a non-baseline algorithm must cite at least one source id")
        if self.segment not in SEGMENTS or self.timeframe not in TIMEFRAMES:
            raise SpecError("unknown segment or timeframe")
        if self.market != MARKET:
            raise SpecError("only the INDIA_NSE market is supported by these specs")
        if self.evidence_tier != "DESCRIPTIVE":
            raise SpecError("specs start at DESCRIPTIVE; promotion is not a spec field")
        if self.segment == "CASH_EQUITY" and self.short_allowed:
            raise SpecError("cash equity cannot be shorted in these specs (ALG-015)")
        if self.parity != PARITY_LABEL:
            raise SpecError("parity may only be the behaviour-adaptation label without a reference run")
        for key in self.parameters:
            low = key.lower()
            if any(bad in low for bad in FORBIDDEN_CLOCK_KEYS):
                raise SpecError(f"fixed foreign session clock {key!r} is not allowed (ALG-054)")
        for value in self.parameters.values():
            if isinstance(value, str) and "ET" in value and ":" in value:
                raise SpecError("fixed US-clock text is not allowed in an NSE spec")

    def spec_sha256(self) -> str:
        body = {
            "algorithm_id": self.algorithm_id,
            "source_ids": list(self.source_ids),
            "family": self.family,
            "version": self.version,
            "segment": self.segment,
            "timeframe": self.timeframe,
            "clock": {"session": self.clock.session, "signal_bar": self.clock.signal_bar, "fill": self.clock.fill.value},
            "parameters": self.parameters,
            "long_allowed": self.long_allowed,
            "short_allowed": self.short_allowed,
            "cost_model_version": self.cost_model_version,
            "evidence_tier": self.evidence_tier,
            "market": self.market,
            "parity": self.parity,
        }
        return sha256_hex(canonical_json(body))


def decision_inputs_ok(values: Sequence[float | None]) -> str:
    """ALG-021: missing, non-finite or zero prices make the decision ABSTAIN, never a trade."""
    for v in values:
        if v is None or isinstance(v, bool) or not math.isfinite(v) or v <= 0:
            return "ABSTAIN"
    return "OK"


@dataclass(frozen=True)
class ReferenceBehaviorFixture:
    """Concept-level check on synthetic bars (ALG-017): expected signal and decision indices are fixed in advance."""

    name: str
    closes: tuple[float, ...]
    expected_signal_indices: tuple[int, ...]
    expected_decision_indices: tuple[int, ...]


def check_fixture(fixture: ReferenceBehaviorFixture, signals: list[int], decisions: list[int]) -> bool:
    return tuple(signals) == fixture.expected_signal_indices and tuple(decisions) == fixture.expected_decision_indices


def cash_baseline_returns(length: int) -> list[float]:
    """Always-hold-cash benchmark (ALG-022): zero return every bar."""
    if length < 0:
        raise SpecError("length must be non-negative")
    return [0.0] * length


def alpha_vs_cash(strategy_returns: list[float]) -> float:
    """Sum of excess over cash. Positive drift in a fixture does not make this positive by itself (ALG-022)."""
    cash = cash_baseline_returns(len(strategy_returns))
    return sum(s - c for s, c in zip(strategy_returns, cash, strict=True))


def order_is_coverable(order: OrderSpec, *, available_quantity: int) -> bool:
    """Unfillable quantities fail closed; partial coverage is reported by the caller (ALG-088)."""
    return 0 < order.quantity <= available_quantity
