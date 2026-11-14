"""Forward-cohort performance report with an explicit session denominator."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
import math

from financial_brain.evaluation.performance import stats
from .evidence_stats import drawdown_stats


class PerformanceReportError(ValueError):
    """The report inputs are ambiguous, incomplete, or outside the frozen schedule."""


def _session_dates(values: Sequence[str]) -> list[str]:
    try:
        dates = [date.fromisoformat(value).isoformat() for value in values]
    except (TypeError, ValueError) as exc:
        raise PerformanceReportError("scheduled sessions must be ISO calendar dates") from exc
    if not dates or dates != sorted(set(dates)):
        raise PerformanceReportError("scheduled sessions must be nonempty, unique and ordered")
    return dates


def _value_map(values: Mapping[str, float | None], dates: list[str], label: str,
               *, nonnegative: bool = False) -> tuple[list[float], list[str]]:
    extra = sorted(set(values) - set(dates))
    if extra:
        raise PerformanceReportError(f"{label} contains unscheduled sessions: {extra}")
    observed, missing = [], []
    for day in dates:
        value = values.get(day)
        if value is None:
            missing.append(day)
            continue
        numeric = float(value)
        if not math.isfinite(numeric) or (nonnegative and numeric < 0):
            raise PerformanceReportError(f"{label} values must be finite" +
                                         (" and nonnegative" if nonnegative else ""))
        if label == "net returns" and numeric <= -1:
            raise PerformanceReportError("net returns must be greater than -100%")
        observed.append(numeric)
    return observed, missing


def _finite(value: float) -> float | None:
    return float(value) if math.isfinite(float(value)) else None


def summarize(
    *,
    scheduled_sessions: Sequence[str],
    net_returns_by_session: Mapping[str, float | None],
    net_alpha_by_session: Mapping[str, float | None],
    one_way_turnover_by_session: Mapping[str, float | None],
    periods_per_year: int = 252,
    annual_risk_free_rate: float = 0.065,
) -> dict:
    """Report net performance, matched net alpha, turnover and sample assumptions.

    ``net_returns_by_session`` and ``net_alpha_by_session`` must include zeroes on
    cash/no-trade sessions. Missing rows stay in the denominator and withhold the
    affected metric. Alpha should come from the same-date, same-risk-budget matcher.
    """
    dates = _session_dates(scheduled_sessions)
    if type(periods_per_year) is not int or periods_per_year < 1 or not math.isfinite(annual_risk_free_rate):
        raise PerformanceReportError("annualization must be a positive integer and risk-free assumption finite")
    returns, missing_returns = _value_map(net_returns_by_session, dates, "net returns")
    alphas, missing_alpha = _value_map(net_alpha_by_session, dates, "net alpha")
    turnovers, missing_turnover = _value_map(one_way_turnover_by_session, dates, "turnover",
                                              nonnegative=True)
    complete_returns = not missing_returns
    metrics = None
    if complete_returns and len(returns) >= 6:
        core = stats(returns, periods=periods_per_year, rf=annual_risk_free_rate, trials=1)
        dd = drawdown_stats(returns)
        metrics = {
            "annualized_return": _finite(core.get("annual_return", float("nan"))),
            "annualized_volatility": _finite(core.get("annual_vol", float("nan"))),
            "sharpe": _finite(core.get("sharpe", float("nan"))),
            "sortino": _finite(core.get("sortino", float("nan"))),
            "max_drawdown": _finite(core.get("max_dd", float("nan"))),
            "profit_factor": _finite(dd["profit_factor"]),
            "mean_daily_net_alpha": sum(alphas) / len(alphas) if not missing_alpha and alphas else None,
            "mean_one_way_turnover": sum(turnovers) / len(turnovers)
                if not missing_turnover and turnovers else None,
            "total_one_way_turnover": sum(turnovers) if not missing_turnover else None,
        }
    if missing_returns:
        status = "INCOMPLETE_RETURNS"
    elif missing_alpha:
        status = "INCOMPLETE_MATCHED_ALPHA"
    elif missing_turnover:
        status = "INCOMPLETE_TURNOVER"
    elif len(returns) < 6:
        status = "INSUFFICIENT_SAMPLE"
    else:
        status = "COMPLETE"
    return {
        "status": status,
        "scheduled_denominator": len(dates),
        "observed_return_sessions": len(returns),
        "missing_return_sessions": missing_returns,
        "missing_alpha_sessions": missing_alpha,
        "missing_turnover_sessions": missing_turnover,
        "sample_start": dates[0],
        "sample_end": dates[-1],
        "sample_calendar_days": (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days + 1,
        "periods_per_year": periods_per_year,
        "annualization_assumption": "geometric annual return and volatility/ratios using periods_per_year",
        "annual_risk_free_rate": annual_risk_free_rate,
        "denominator_policy": "all scheduled sessions; zero-return cash days included; missing evidence never dropped",
        "metrics": metrics,
    }
