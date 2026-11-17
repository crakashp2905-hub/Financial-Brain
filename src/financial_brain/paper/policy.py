"""Central five-desk paper policy and small cross-cutting helpers
(FB-0006, FB-0460, FB-0461, FB-0917, FB-1008).

``PHYSICAL_ORDERS_ENABLED`` mirrors ``forward_edge.modes`` and is asserted False by every five-desk
entry point through ``assert_paper_only``. No credential, UI toggle or environment variable changes it.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from financial_brain.forward_edge import modes

PHYSICAL_ORDERS_ENABLED = False


class PolicyError(RuntimeError):
    pass


def assert_paper_only() -> None:
    if PHYSICAL_ORDERS_ENABLED or modes.PHYSICAL_ORDERS_ENABLED:
        raise PolicyError("physical orders must stay disabled for the five-desk pipeline")


def size_position(*, risk_budget: Decimal, entry: Decimal, stop: Decimal, cash: Decimal, lot: int,
                  max_notional: Decimal, displayed_depth: int, participation: Decimal,
                  correlated_exposure: Decimal, correlated_cap: Decimal) -> int:
    """Smallest of risk, cash, notional, depth and correlation capacity, in whole lots (FB-0460).

    The stop-distance risk is *planned*; gaps can exceed it.
    """
    per_unit = abs(entry - stop)
    if per_unit <= 0 or entry <= 0 or lot < 1:
        raise PolicyError("invalid sizing inputs")
    caps = [risk_budget / per_unit, cash / entry, max_notional / entry,
            Decimal(displayed_depth) * participation,
            max(correlated_cap - correlated_exposure, Decimal(0)) / entry]
    units = int(min(caps))
    return units - units % lot


def round_and_revalidate(side: str, entry: Decimal, stop: Decimal, target: Decimal, tick: Decimal,
                         min_rr: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """Round to tick conservatively (stop away from entry, target toward it) and re-check R:R (FB-0461)."""
    def q(x: Decimal, mode: str) -> Decimal:
        return (x / tick).to_integral_value(rounding=mode) * tick
    if side == "BUY":
        e, s, t = q(entry, ROUND_CEILING), q(stop, ROUND_FLOOR), q(target, ROUND_FLOOR)
        rr = (t - e) / (e - s) if e > s else Decimal(0)
    elif side == "SELL_SHORT":
        e, s, t = q(entry, ROUND_FLOOR), q(stop, ROUND_CEILING), q(target, ROUND_CEILING)
        rr = (e - t) / (s - e) if s > e else Decimal(0)
    else:
        raise PolicyError(f"unknown side {side}")
    if rr < min_rr:
        raise PolicyError(f"reward:risk {rr:.2f} below {min_rr} after rounding")
    return e, s, t


def reconcile_handler(check_journal: Callable[[], None], gate: Callable[[], tuple[str, list[str]]],
                      record: Callable[[date, str, list[str]], None]) -> Callable[[date], None]:
    """Daily reconcile phase: journal balance then session gate; result recorded (FB-0917/FB-1008)."""
    def h(d: date) -> None:
        check_journal()
        status, problems = gate()
        record(d, status, problems)
    return h
