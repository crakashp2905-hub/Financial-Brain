"""Date- and risk-budget-matched net performance inputs for forward cohorts."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import math

from .evidence_stats import StatsError, daily_returns


class MatchedPerformanceError(ValueError):
    """Inputs cannot support a fair same-session, same-risk comparison."""


@dataclass(frozen=True)
class DailyNetReturn:
    session_date: str
    net_return_fraction: Decimal
    risk_budget_inr: Decimal


def _decimal(value: Decimal | int | float | str, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MatchedPerformanceError(f"{label} must be a finite decimal") from exc
    if not parsed.is_finite():
        raise MatchedPerformanceError(f"{label} must be a finite decimal")
    return parsed


def from_nav(
    session_dates: Sequence[str],
    nav_inr: Sequence[Decimal | int | float | str],
    external_flows_inr: Sequence[Decimal | int | float | str],
    risk_budgets_inr: Sequence[Decimal | int | float | str],
) -> list[DailyNetReturn]:
    """Build net return observations; flows are cash movements between consecutive closes.

    The caller must provide NAV after all realized/unrealized P&L, fees, charges,
    financing and marks. A cash day stays in the output with a zero return.
    """
    if len(session_dates) < 2 or len(nav_inr) != len(session_dates):
        raise MatchedPerformanceError("NAV needs one value per session date and at least two dates")
    if len(external_flows_inr) != len(session_dates) - 1:
        raise MatchedPerformanceError("one external cash flow is required per return interval")
    if len(risk_budgets_inr) != len(session_dates) - 1:
        raise MatchedPerformanceError("one declared risk budget is required per return interval")
    try:
        dates = [date.fromisoformat(value).isoformat() for value in session_dates]
    except (TypeError, ValueError) as exc:
        raise MatchedPerformanceError("session dates must be ISO calendar dates") from exc
    if dates != sorted(set(dates)):
        raise MatchedPerformanceError("session dates must be unique and strictly increasing")
    nav = [_decimal(value, "NAV") for value in nav_inr]
    flows = [_decimal(value, "external flow") for value in external_flows_inr]
    budgets = [_decimal(value, "risk budget") for value in risk_budgets_inr]
    if any(value <= 0 for value in nav):
        raise MatchedPerformanceError("NAV must remain positive")
    if any(value <= 0 for value in budgets):
        raise MatchedPerformanceError("risk budgets must remain positive, including cash days")
    try:
        returns = daily_returns([float(value) for value in nav],
                                [float(value) for value in flows])
    except StatsError as exc:
        raise MatchedPerformanceError(str(exc)) from exc
    if not all(math.isfinite(value) for value in returns):
        raise MatchedPerformanceError("NAV and flows produce a non-finite return")
    return [DailyNetReturn(day, Decimal(str(ret)), budget)
            for day, ret, budget in zip(dates[1:], returns, budgets)]


def matched_excess(
    strategy: Sequence[DailyNetReturn],
    benchmark: Sequence[DailyNetReturn],
) -> dict:
    """Compute daily net alpha only when both books share dates and risk budgets."""
    if not strategy or not benchmark:
        raise MatchedPerformanceError("strategy and benchmark observations are required")

    def indexed(rows: Sequence[DailyNetReturn], name: str) -> dict[str, DailyNetReturn]:
        keys = [row.session_date for row in rows]
        if keys != sorted(set(keys)):
            raise MatchedPerformanceError(f"{name} dates must be unique and strictly increasing")
        return {row.session_date: row for row in rows}

    strategy_by_day = indexed(strategy, "strategy")
    benchmark_by_day = indexed(benchmark, "benchmark")
    if strategy_by_day.keys() != benchmark_by_day.keys():
        missing_benchmark = sorted(strategy_by_day.keys() - benchmark_by_day.keys())
        missing_strategy = sorted(benchmark_by_day.keys() - strategy_by_day.keys())
        raise MatchedPerformanceError(
            f"date mismatch; missing benchmark={missing_benchmark}, missing strategy={missing_strategy}")
    rows = []
    for day in strategy_by_day:
        s, b = strategy_by_day[day], benchmark_by_day[day]
        if s.risk_budget_inr != b.risk_budget_inr:
            raise MatchedPerformanceError(f"risk budget mismatch on {day}")
        rows.append({"session_date": day,
                     "strategy_net_return": str(s.net_return_fraction),
                     "benchmark_net_return": str(b.net_return_fraction),
                     "net_alpha": str(s.net_return_fraction - b.net_return_fraction),
                     "risk_budget_inr": str(s.risk_budget_inr)})
    alpha = [Decimal(row["net_alpha"]) for row in rows]
    return {"metric_version": "matched_net_alpha_v1", "n_sessions": len(rows),
            "mean_daily_net_alpha": str(sum(alpha) / len(alpha)), "observations": rows}
