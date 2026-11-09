"""Central pre-trade risk gate (FB-0270..0283, FB-0285, FB-0289, FB-0290, FB-0291).

* ``Policy`` is frozen. A strategy can request a smaller order but can never raise a limit.
* Exposure includes pending (not yet filled) orders, so concurrent orders cannot jointly exceed a limit.
* Drawdown and daily-loss breaches latch: they stay tripped until ``reset`` by a human actor. An agent actor is
  refused, the same rule the existing kill switch applies.
* Stale or invalid data blocks new entries while existing exposure stays visible.
* Every denial returns a reason code and a ranked list; ``NO_TRADE`` is a valid outcome.
* The latched state round-trips through JSON, so a restart still refuses orders.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

ALLOW, DENY = "ALLOW", "DENY"


class RiskError(PermissionError):
    pass


@dataclass(frozen=True)
class Policy:
    max_order_notional: Decimal
    max_position_units: int
    max_gross_exposure: Decimal
    max_drawdown: Decimal  # e.g. 0.10 = 10% below peak latches HALT
    max_daily_loss: Decimal
    max_stale_seconds: int

    def __post_init__(self) -> None:
        for name in ("max_order_notional", "max_gross_exposure", "max_daily_loss"):
            if getattr(self, name) <= 0:
                raise RiskError(f"{name} must be positive")
        if not (Decimal(0) < self.max_drawdown < Decimal(1)):
            raise RiskError("max_drawdown must be between 0 and 1")


@dataclass(frozen=True)
class OrderRequest:
    instrument: str
    side: str
    quantity: int
    price: Decimal
    multiplier: Decimal = Decimal(1)


@dataclass(frozen=True)
class MarketState:
    equity: Decimal
    peak_equity: Decimal
    day_pnl: Decimal
    gross_exposure: Decimal  # filled plus pending notional
    positions: dict[str, int]
    pending: dict[str, int]
    data_age_seconds: int
    data_valid: bool


@dataclass
class Latch:
    tripped: list[str] = field(default_factory=list)

    def trip(self, reason: str) -> None:
        if reason not in self.tripped:
            self.tripped.append(reason)

    def reset(self, *, actor: str, reason: str) -> None:
        if not actor or actor.startswith("agent:"):
            raise RiskError("only a human actor may reset a risk latch")
        if not reason:
            raise RiskError("a reset needs a reason")
        self.tripped.clear()

    def to_json(self) -> str:
        return json.dumps({"tripped": self.tripped})

    @classmethod
    def from_json(cls, text: str) -> Latch:
        return cls(tripped=list(json.loads(text)["tripped"]))

    def save(self, path: Path) -> None:
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Latch:
        return cls.from_json(path.read_text(encoding="utf-8")) if path.exists() else cls()


def evaluate(policy: Policy, order: OrderRequest, state: MarketState, latch: Latch) -> dict[str, Any]:
    """Return {decision, reasons (ranked, most severe first)}. Latched and data failures are ranked first."""
    reasons: list[str] = []
    if latch.tripped:
        reasons.append("LATCHED:" + "+".join(latch.tripped))
    if not state.data_valid:
        reasons.append("INVALID_DATA")
    if state.data_age_seconds > policy.max_stale_seconds:
        reasons.append("STALE_DATA")
    if order.quantity <= 0 or order.price <= 0:
        reasons.append("INVALID_ORDER")
    notional = order.price * Decimal(order.quantity) * order.multiplier
    if notional > policy.max_order_notional:
        reasons.append("ORDER_NOTIONAL_CAP")
    sign = 1 if order.side == "BUY" else -1
    projected = state.positions.get(order.instrument, 0) + state.pending.get(order.instrument, 0)
    if abs(projected + sign * order.quantity) > policy.max_position_units:
        reasons.append("POSITION_CAP")
    if state.gross_exposure + notional > policy.max_gross_exposure:
        reasons.append("GROSS_EXPOSURE_CAP")
    if state.peak_equity > 0 and (state.peak_equity - state.equity) / state.peak_equity > policy.max_drawdown:
        reasons.append("DRAWDOWN_BREACH")
    if state.day_pnl <= -policy.max_daily_loss:
        reasons.append("DAILY_LOSS_BREACH")
    if reasons:
        return {"decision": DENY, "reasons": reasons}
    return {"decision": ALLOW, "reasons": []}


def apply_breaches(policy: Policy, state: MarketState, latch: Latch) -> None:
    """Latch drawdown and daily-loss breaches so they persist until a human resets them."""
    if state.peak_equity > 0 and (state.peak_equity - state.equity) / state.peak_equity > policy.max_drawdown:
        latch.trip("DRAWDOWN")
    if state.day_pnl <= -policy.max_daily_loss:
        latch.trip("DAILY_LOSS")
