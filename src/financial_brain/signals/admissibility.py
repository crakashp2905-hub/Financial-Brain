"""Executable expected value and the TRADE / WATCH / REJECT / CASH decision (checklist section 16).

A candidate becomes an eligible order only with an immutable economics receipt built from
pre-decision inputs: hit probabilities for stop, target and time exit, executable entry from a
quote, and an explicit cost model. Missing costs, missing quotes or NaN inputs never default to a
tradable outcome. Probabilities are labelled CALIBRATED only when the caller says they passed an
out-of-time calibration check; otherwise the receipt says UNCALIBRATED and the decision is WATCH.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class Decision(str, Enum):
    TRADE = "TRADE"
    WATCH = "WATCH"
    REJECT = "REJECT"
    CASH = "CASH"


class Reason(str, Enum):
    OK = "OK"
    NO_EDGE = "NO_EDGE"
    COST = "COST"
    STALE_FEED = "STALE_FEED"
    MISSING_INPUT = "MISSING_INPUT"
    CONFIDENCE_LOW = "CONFIDENCE_LOW"
    UNCALIBRATED = "UNCALIBRATED"
    SPREAD = "SPREAD"
    EXPIRED = "EXPIRED"
    HALTED = "HALTED"
    BUDGET = "BUDGET"


@dataclass(frozen=True)
class Scenario:
    """Pre-decision outcome model for one trade, per unit, in rupees."""

    p_target: Decimal
    p_stop: Decimal
    reward: Decimal          # move to target
    risk: Decimal            # move to stop (positive number)
    time_exit_mean: Decimal  # expected move if neither hits (may be negative)
    gap_extra_loss: Decimal  # expected extra loss beyond the stop from gaps (>= 0)
    calibrated: bool
    samples: int


@dataclass(frozen=True)
class Costs:
    """Round-trip cost per unit: brokerage, taxes, charges, spread and impact. Version recorded."""

    per_unit: Decimal
    model_version: str


@dataclass(frozen=True)
class Receipt:
    decision: Decision
    reason: Reason
    net_ev_per_unit: Decimal | None
    net_ev_total: Decimal | None
    risk_at_stop_inr: Decimal | None
    break_even_p_target: Decimal | None
    cost_model: str | None
    calibrated: bool


def _finite(*vals: Decimal | None) -> bool:
    return all(v is not None and isinstance(v, Decimal) and v.is_finite() for v in vals)


def net_ev(s: Scenario, costs: Costs) -> Decimal:
    """Expected net rupees per unit. Raises on probabilities that do not form a distribution."""
    if not _finite(s.p_target, s.p_stop, s.reward, s.risk, s.time_exit_mean, s.gap_extra_loss,
                   costs.per_unit):
        raise ValueError("non-finite input")
    if s.p_target < 0 or s.p_stop < 0 or s.p_target + s.p_stop > 1:
        raise ValueError("hit probabilities must be in [0, 1] and sum to at most 1")
    if s.risk <= 0 or s.reward <= 0 or s.gap_extra_loss < 0 or costs.per_unit < 0:
        raise ValueError("reward and risk must be positive; gap loss and costs non-negative")
    p_time = 1 - s.p_target - s.p_stop
    gross = s.p_target * s.reward - s.p_stop * (s.risk + s.gap_extra_loss) + p_time * s.time_exit_mean
    return gross - costs.per_unit


def decide(s: Scenario | None, costs: Costs | None, *, units: int, quote_ok: bool,
           spread_ok: bool, expired: bool, halted: bool, budget_ok: bool,
           hurdle_per_unit: Decimal, min_samples: int) -> Receipt:
    """Reason-coded decision. Any missing or failing input lands on a non-trading outcome."""
    def out(d: Decision, r: Reason, ev: Decimal | None = None) -> Receipt:
        risk = s.risk * units if s is not None and _finite(s.risk) else None
        be = None
        if s is not None and costs is not None and _finite(s.reward, s.risk, costs.per_unit):
            be = (s.risk + costs.per_unit) / (s.reward + s.risk)
        return Receipt(d, r, ev, ev * units if ev is not None else None, risk, be,
                       costs.model_version if costs else None, bool(s and s.calibrated))

    if halted:
        return out(Decision.REJECT, Reason.HALTED)
    if expired:
        return out(Decision.REJECT, Reason.EXPIRED)
    if not quote_ok:
        return out(Decision.REJECT, Reason.STALE_FEED)
    if s is None or costs is None:
        return out(Decision.REJECT, Reason.MISSING_INPUT)
    try:
        ev = net_ev(s, costs)
    except ValueError:
        return out(Decision.REJECT, Reason.MISSING_INPUT)
    if not spread_ok:
        return out(Decision.WATCH, Reason.SPREAD, ev)
    if s.samples < min_samples:
        return out(Decision.WATCH, Reason.CONFIDENCE_LOW, ev)
    if not s.calibrated:
        return out(Decision.WATCH, Reason.UNCALIBRATED, ev)
    if ev <= 0:
        return out(Decision.CASH, Reason.NO_EDGE if ev + costs.per_unit <= 0 else Reason.COST, ev)
    if ev <= hurdle_per_unit:
        return out(Decision.CASH, Reason.COST, ev)
    if not budget_ok:
        return out(Decision.WATCH, Reason.BUDGET, ev)
    return out(Decision.TRADE, Reason.OK, ev)
