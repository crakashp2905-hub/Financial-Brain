"""Convert an index direction forecast into a listed option candidate or NO_TRADE (checklist 24D/24E).

A directional probability is not an option edge. Each listed contract's expected premium change is
computed from a distribution of underlying moves (delta-gamma), minus theta over the horizon, minus a
volatility-crush allowance, and the round-trip cost of crossing the actual bid/ask. Only contracts
present in the supplied listed set, unexpired, with fresh two-sided quotes and depth are considered.
The best candidate must have positive expected net P&L per lot, else the result is NO_TRADE.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum

OPTIONABLE = frozenset({"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "NIFTYFPI"})


class IndexSignalState(str, Enum):
    RESEARCH_ONLY = "RESEARCH_ONLY"
    NO_TRADE = "NO_TRADE"
    WATCH = "WATCH"
    PAPER_ELIGIBLE = "PAPER_ELIGIBLE"
    UNFILLED = "UNFILLED"
    OPEN = "OPEN"
    PARTIALLY_EXITED = "PARTIALLY_EXITED"
    CLOSED = "CLOSED"
    UNPRICED = "UNPRICED"


@dataclass(frozen=True)
class Contract:
    token: str
    underlying: str
    expiry: date
    strike: float
    kind: str           # CE or PE
    lot_size: int
    bid: float
    ask: float
    bid_qty: int
    ask_qty: int
    quote_at: datetime
    delta: float
    gamma: float
    theta_per_day: float
    vega: float


@dataclass(frozen=True)
class Candidate:
    state: IndexSignalState
    token: str | None
    expected_net_per_lot: float | None
    reason: str


def _usable(c: Contract, now: datetime, max_age: timedelta, listed: set[str], lots: int) -> str | None:
    if c.token not in listed:
        return "not in the listed instrument master"
    if c.expiry < now.date():
        return "expired"
    if c.bid <= 0 or c.ask <= c.bid:
        return "no two-sided quote"
    if now - c.quote_at > max_age or c.quote_at > now:
        return "stale or future quote"
    if c.ask_qty < lots * c.lot_size:
        return "insufficient displayed depth"
    return None


def expected_option_pnl(c: Contract, moves: list[tuple[float, float]], horizon_days: float,
                        iv_change: float, fees_per_lot: float) -> float:
    """Expected net P&L per lot for buying at ask and exiting at bid after the horizon.

    ``moves`` are (probability, index point move) pairs summing to 1.
    """
    if abs(sum(p for p, _ in moves) - 1) > 1e-9:
        raise ValueError("move probabilities must sum to 1")
    exp_change = sum(p * (c.delta * m + 0.5 * c.gamma * m * m) for p, m in moves)
    exp_change += c.theta_per_day * horizon_days + c.vega * iv_change
    spread = c.ask - c.bid
    return (exp_change - spread) * c.lot_size - fees_per_lot


def choose(underlying: str, p_up: float, p_down: float, moves: list[tuple[float, float]],
           contracts: list[Contract], listed: set[str], now: datetime, *, max_age: timedelta,
           horizon_days: float, iv_change: float, fees_per_lot: float, lots: int = 1,
           min_prob: float = 0.5) -> Candidate:
    if underlying not in OPTIONABLE:
        return Candidate(IndexSignalState.RESEARCH_ONLY, None, None, "no listed options")
    side = "CE" if p_up >= p_down else "PE"
    if max(p_up, p_down) < min_prob:
        return Candidate(IndexSignalState.NO_TRADE, None, None, "no directional lean")
    best: tuple[float, Contract] | None = None
    skipped = []
    for c in contracts:
        if c.underlying != underlying or c.kind != side:
            continue
        why = _usable(c, now, max_age, listed, lots)
        if why:
            skipped.append(why)
            continue
        ev = expected_option_pnl(c, moves, horizon_days, iv_change, fees_per_lot)
        if best is None or ev > best[0]:
            best = (ev, c)
    if best is None:
        return Candidate(IndexSignalState.UNPRICED, None, None,
                         "; ".join(sorted(set(skipped))) or "no contract")
    if best[0] <= 0:
        return Candidate(IndexSignalState.NO_TRADE, best[1].token, best[0], "expected net P&L not positive")
    return Candidate(IndexSignalState.PAPER_ELIGIBLE, best[1].token, best[0], "positive expected net")
