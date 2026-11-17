"""Desk scorecards, flow-adjusted returns and frozen strategy status (checklist sections 21 and 22).

* Trade statistics per desk: profit factor, expectancy, median and tail loss, win rate, averages.
* Time-weighted return chains sub-period returns between external flows, so contributions and SIPs
  never count as performance; money-weighted return (XIRR) is reported separately.
* Desk P&L must sum to consolidated P&L; a mismatch raises.
* Strategy status is EXPLORATORY / COLLECTING / FAILING / FORWARD_VERIFIED by frozen rules on
  forward observations only, with a session-block bootstrap lower bound for dependent returns.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from statistics import median


class ScoreError(ValueError):
    pass


@dataclass(frozen=True)
class TradeStats:
    trades: int
    win_rate: float
    expectancy: float
    profit_factor: float | None
    avg_win: float | None
    avg_loss: float | None
    median: float
    tail_loss_5pct: float


def trade_stats(pnls: list[float]) -> TradeStats:
    if not pnls:
        raise ScoreError("no closed trades")
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_loss = -sum(losses)
    s = sorted(pnls)
    k = max(1, int(len(s) * 0.05))
    return TradeStats(
        trades=len(pnls), win_rate=len(wins) / len(pnls), expectancy=sum(pnls) / len(pnls),
        profit_factor=(sum(wins) / gross_loss) if gross_loss > 0 else None,
        avg_win=sum(wins) / len(wins) if wins else None,
        avg_loss=sum(losses) / len(losses) if losses else None,
        median=median(pnls), tail_loss_5pct=sum(s[:k]) / k)


def time_weighted_return(values: list[tuple[date, float, float]]) -> float:
    """``values`` are (day, NAV before flow, external flow at end of day). Flows are not return."""
    if len(values) < 2:
        raise ScoreError("need at least two valuations")
    growth = 1.0
    prev_nav = values[0][1] + values[0][2]
    for _, nav, flow in values[1:]:
        if prev_nav <= 0:
            raise ScoreError("non-positive starting value in a sub-period")
        growth *= nav / prev_nav
        prev_nav = nav + flow
    return growth - 1


def xirr(flows: list[tuple[date, float]]) -> float:
    """Money-weighted annual return. Investor flows negative, final value positive."""
    if not any(f < 0 for _, f in flows) or not any(f > 0 for _, f in flows):
        raise ScoreError("xirr needs both outflows and inflows")
    t0 = flows[0][0]

    def npv(r: float) -> float:
        return sum(f / (1 + r) ** ((d - t0).days / 365.0) for d, f in flows)

    lo, hi = -0.9999, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def reconcile(desk_pnl: dict[str, Decimal], consolidated: Decimal) -> None:
    """Desk P&L must equal consolidated P&L exactly (FB-2108)."""
    total = sum(desk_pnl.values(), Decimal(0))
    if total != consolidated:
        raise ScoreError(f"desk P&L {total} != consolidated {consolidated}")


def block_bootstrap_lower(returns_by_session: list[list[float]], *, alpha: float = 0.05,
                          draws: int = 2000, seed: int = 7) -> float:
    """Lower confidence bound on the mean return, resampling whole sessions (dependent returns)."""
    if len(returns_by_session) < 2:
        raise ScoreError("need at least two sessions")
    rng = random.Random(seed)
    n = len(returns_by_session)
    means = []
    for _ in range(draws):
        pick = [returns_by_session[rng.randrange(n)] for _ in range(n)]
        flat = [r for s in pick for r in s]
        means.append(sum(flat) / len(flat) if flat else 0.0)
    means.sort()
    return means[int(alpha * draws)]


class Status(str, Enum):
    EXPLORATORY = "EXPLORATORY"
    COLLECTING = "COLLECTING"
    FAILING = "FAILING"
    FORWARD_VERIFIED = "FORWARD_VERIFIED"


@dataclass(frozen=True)
class FrozenRule:
    """Preregistered before the cohort starts; changing any field is a new version (FB-2201)."""

    version: str
    min_sessions: int
    max_drawdown: float
    registered: bool


def strategy_status(rule: FrozenRule, forward_sessions: list[list[float]], drawdown: float,
                    benchmark_mean: float) -> Status:
    """Status from forward observations only. Backtests never enter here."""
    if not rule.registered:
        return Status.EXPLORATORY
    if drawdown > rule.max_drawdown:
        return Status.FAILING
    if len(forward_sessions) < rule.min_sessions:
        return Status.COLLECTING
    excess = [[r - benchmark_mean for r in s] for s in forward_sessions]
    lower = block_bootstrap_lower(excess)
    return Status.FORWARD_VERIFIED if lower > 0 else Status.FAILING
