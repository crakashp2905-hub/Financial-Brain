"""Consolidated, cost-aware paper-book performance and capacity diagnostics."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date
from itertools import groupby


class PerformanceReportError(ValueError):
    """Invalid or incomplete paper performance evidence."""


@dataclass(frozen=True)
class PerformanceSession:
    session: date
    net_return: float
    traded_notional: float
    opening_equity: float
    gross_exposure: float
    net_exposure: float
    supported_capacity_notional: float


@dataclass(frozen=True)
class PerformanceTrade:
    net_pnl_inr: float
    traded_notional: float


def _compound(values: list[float]) -> float:
    return math.prod(1.0 + value for value in values) - 1.0


def build_performance_report(*, sessions: tuple[PerformanceSession, ...],
                              trades: tuple[PerformanceTrade, ...],
                              risk_free_daily: float = 0.0,
                              annualization_sessions: int = 250) -> dict[str, object]:
    """Summarize returns, tail/drawdown, turnover, trade quality, exposure and capacity.

    Requires every scheduled session, including cash days, and already-net returns. This
    deterministic descriptive report does not provide inferential confidence intervals.
    """
    if len(sessions) < 2 or not trades:
        raise PerformanceReportError("at least two sessions and one resolved trade are required")
    dates = tuple(row.session for row in sessions)
    if tuple(sorted(dates)) != dates or len(set(dates)) != len(dates):
        raise PerformanceReportError("sessions must be unique and chronological")
    if type(annualization_sessions) is not int or annualization_sessions <= 0:
        raise PerformanceReportError("annualization sessions must be a positive integer")
    values = [risk_free_daily, *(value for row in sessions for value in (
        row.net_return, row.traded_notional, row.opening_equity, row.gross_exposure,
        row.net_exposure, row.supported_capacity_notional)),
        *(value for trade in trades for value in (trade.net_pnl_inr, trade.traded_notional))]
    if any(not math.isfinite(float(value)) for value in values):
        raise PerformanceReportError("all performance inputs must be finite")
    if any(row.net_return <= -1 for row in sessions):
        raise PerformanceReportError("session loss cannot exhaust equity")
    if any(row.traded_notional < 0 or row.opening_equity <= 0 or row.gross_exposure < 0
           or row.supported_capacity_notional < 0 for row in sessions):
        raise PerformanceReportError("notional, equity, exposure and capacity must be valid")
    if any(trade.traded_notional <= 0 for trade in trades):
        raise PerformanceReportError("resolved trade notional must be positive")

    returns = [float(row.net_return) for row in sessions]
    excess = [value - risk_free_daily for value in returns]
    mean_excess = sum(excess) / len(excess)
    sample_var = sum((value - mean_excess) ** 2 for value in excess) / (len(excess) - 1)
    daily_std = math.sqrt(sample_var)
    downside_dev = math.sqrt(sum(min(value, 0.0) ** 2 for value in excess) / len(excess))
    equity = peak = 1.0
    max_dd = 0.0
    max_dd_duration = current_dd_duration = 0
    for value in returns:
        equity *= 1 + value
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak
        if drawdown > 0:
            current_dd_duration += 1
            max_dd_duration = max(max_dd_duration, current_dd_duration)
        else:
            current_dd_duration = 0
        max_dd = max(max_dd, drawdown)

    weeks = []
    months = []
    for _, group in groupby(sessions, key=lambda row: (row.session.isocalendar().year,
                                                       row.session.isocalendar().week)):
        weeks.append(_compound([row.net_return for row in group]))
    for _, group in groupby(sessions, key=lambda row: (row.session.year, row.session.month)):
        months.append(_compound([row.net_return for row in group]))
    bottom_count = max(1, math.ceil(0.05 * len(returns)))
    tail = sorted(returns)[:bottom_count]
    longest_loss_run = max((sum(1 for _ in group) for value, group in
                            groupby(returns, key=lambda value: value < 0) if value), default=0)
    trade_bps = [trade.net_pnl_inr / trade.traded_notional * 10_000 for trade in trades]
    total_capacity = sum(row.supported_capacity_notional for row in sessions)
    total_traded = sum(row.traded_notional for row in sessions)
    report: dict[str, object] = {
        "sessions": len(sessions),
        "resolved_trades": len(trades),
        "compounded_net_return": _compound(returns),
        "annualized_sharpe": (mean_excess / daily_std * math.sqrt(annualization_sessions)
                              if daily_std > 0 else None),
        "annualized_sortino": (mean_excess / downside_dev * math.sqrt(annualization_sessions)
                               if downside_dev > 0 else None),
        "max_drawdown": max_dd,
        "max_drawdown_duration_sessions": max_dd_duration,
        "worst_day_net_return": min(returns),
        "worst_week_net_return": min(weeks),
        "worst_month_net_return": min(months),
        "longest_consecutive_loss_days": longest_loss_run,
        "historical_cvar_95_daily_return": sum(tail) / len(tail),
        "cumulative_turnover_x_average_opening_equity": total_traded / (
            sum(row.opening_equity for row in sessions) / len(sessions)),
        "mean_net_basis_points_per_trade": sum(trade_bps) / len(trade_bps),
        "notional_weighted_net_basis_points_per_trade": (
            sum(trade.net_pnl_inr for trade in trades) / sum(trade.traded_notional for trade in trades) * 10_000),
        "average_gross_exposure": sum(row.gross_exposure for row in sessions) / len(sessions),
        "maximum_gross_exposure": max(row.gross_exposure for row in sessions),
        "average_net_exposure": sum(row.net_exposure for row in sessions) / len(sessions),
        "maximum_absolute_net_exposure": max(abs(row.net_exposure) for row in sessions),
        "capacity_supported_notional": total_capacity,
        "traded_notional": total_traded,
        "capacity_utilization": total_traded / total_capacity if total_capacity > 0 else None,
        "interpretation": "descriptive, after-cost; unresolved trade outcomes must be reported separately",
        "confidence_intervals": "NOT_COMPUTED_USE_STATIONARY_BLOCK_RISK_REPORT",
    }
    report["report_sha256"] = hashlib.sha256(json.dumps(
        report, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()
    return report
