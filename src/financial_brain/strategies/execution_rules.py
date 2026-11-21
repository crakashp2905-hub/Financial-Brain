"""Paper execution rules and accounting separations (ALG-084..094).

Signals, intents, accepted orders, simulated fills and realised outcomes are separate types. Realised P&L requires a
fill record. Ambiguous intrabar paths take the adverse branch. Gaps through a stop fill at the adverse open. Nothing
here sends an order anywhere: these functions compute paper outcomes only.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from financial_brain.strategies.specs import decision_inputs_ok


class NoFillRecord(RuntimeError):
    """Realised P&L was requested without a ledger-backed fill (ALG-084)."""


@dataclass(frozen=True)
class Signal:
    index: int
    side: str


@dataclass(frozen=True)
class Intent:
    signal: Signal
    quantity: int


@dataclass(frozen=True)
class AcceptedOrder:
    intent: Intent
    accepted_at: int


@dataclass(frozen=True)
class FillRecord:
    order: AcceptedOrder
    fill_index: int
    price: float
    quantity: int
    unfilled: int = 0
    cost: float = 0.0


@dataclass
class Outcome:
    fill: FillRecord | None
    realised: float | None = None
    notes: list[str] = field(default_factory=list)


def realised_pnl(outcome: Outcome) -> float:
    """Realised P&L is only defined when the outcome has a fill (ALG-084)."""
    if outcome.fill is None or outcome.realised is None:
        raise NoFillRecord("no ledger-backed fill: realised P&L is undefined")
    return outcome.realised


def next_open_fill_index(signal_index: int) -> int:
    """A close-triggered signal fills no earlier than the following bar's open (ALG-085)."""
    return signal_index + 1


def resolve_intrabar(bar_high: float, bar_low: float, stop: float, target: float, *, side: str) -> str:
    """When both stop and target sit inside one bar, the order is unknown. Take the adverse branch (ALG-086)."""
    if decision_inputs_ok([bar_high, bar_low, stop, target]) != "OK":
        return "NO_FILL"
    if side == "LONG":
        hit_stop = bar_low <= stop
        hit_target = bar_high >= target
    else:
        hit_stop = bar_high >= stop
        hit_target = bar_low <= target
    if hit_stop and hit_target:
        return "STOP"  # adverse by rule; intrabar evidence would be required to do otherwise
    if hit_stop:
        return "STOP"
    if hit_target:
        return "TARGET"
    return "NONE"


def round_to_tick(price: float, tick: float, *, direction: str) -> float:
    """Round toward the adverse side of the tick grid (ALG-087)."""
    if tick <= 0 or decision_inputs_ok([price]) != "OK":
        raise ValueError("positive price and tick are required")
    steps = price / tick
    n = int(steps) if direction == "down" else -int(-steps // 1)
    return round(n * tick, 10)


def stop_fill_price(open_: float, stop: float, *, side: str, tick: float | None = None) -> float:
    """A gap through the stop fills at the open, which is the adverse executable price (ALG-087)."""
    if side == "LONG":
        price = min(open_, stop)
    else:
        price = max(open_, stop)
    if tick:
        price = round_to_tick(price, tick, direction="down" if side == "LONG" else "up")
    return price


def participation_fill(quantity: int, volume: float, cap: float) -> tuple[int, int]:
    """Fill at most ``cap`` of the bar's volume; the rest is unfilled and reported (ALG-088)."""
    if quantity < 0 or volume < 0 or not 0 <= cap <= 1:
        raise ValueError("invalid quantity, volume or cap")
    fillable = int(volume * cap)
    filled = min(quantity, fillable)
    return filled, quantity - filled


def must_flat(*, now_hhmm: str, flat_at: str, product: str, expiry: date | None, today: date) -> bool:
    """Intraday products are flat by the flat time. Expiring contracts are flat on or after expiry (ALG-057, ALG-089)."""
    if product == "CASH_EQUITY_INTRADAY":
        return now_hhmm >= flat_at
    if product in {"INDEX_FUTURES", "STOCK_FUTURES"}:
        if expiry is None:
            raise ValueError("futures need an expiry")
        return today >= expiry or now_hhmm >= flat_at
    raise ValueError("unknown product")


def viewer_summary(*, open_gross_mtm: float, closed_net: float, modelled_closeout_cost: float, capital_at_risk: float) -> dict[str, float]:
    """The four figures are returned separately; a total never adds open gross MTM to closed net (ALG-090)."""
    return {
        "open_gross_mtm": open_gross_mtm,
        "closed_net": closed_net,
        "modelled_closeout_cost": modelled_closeout_cost,
        "capital_at_risk": capital_at_risk,
    }


def coverage_score(observed: int, replay: int, fixed: int) -> dict[str, Any]:
    """Share of each cost input by source, with the exact denominator (ALG-092)."""
    total = observed + replay + fixed
    if total <= 0:
        return {"denominator": 0, "observed": 0.0, "replay": 0.0, "fixed": 0.0}
    return {"denominator": total, "observed": observed / total, "replay": replay / total, "fixed": fixed / total}


def usable_for_paper(meta: dict[str, Any]) -> bool:
    """A strategy missing its instrument or cost identity is unavailable, not silently run (ALG-093)."""
    return bool(meta.get("instrument")) and bool(meta.get("cost_model_version")) and bool(meta.get("spec_sha256"))


def monte_carlo_drill(seed: int, *, n: int = 100, gap_prob: float = 0.02, shock_scale: float = 3.0) -> dict[str, float]:
    """Repeatable synthetic gap and volatility drill under a fixed seed (ALG-094)."""
    rng = random.Random(seed)
    worst = 0.0
    gaps = 0
    for _ in range(n):
        r = rng.gauss(0.0, 0.01)
        if rng.random() < gap_prob:
            gaps += 1
            r -= shock_scale * 0.02
        worst = min(worst, r)
    return {"seed": float(seed), "gaps": float(gaps), "worst_return": worst}
