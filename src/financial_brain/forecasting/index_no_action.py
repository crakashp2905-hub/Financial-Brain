"""Cash/no-options benchmark for evaluating forecast-driven option decisions."""
from __future__ import annotations

from math import isfinite
from typing import Any


class NoActionError(ValueError):
    """Invalid or incomplete option-versus-cash comparison."""


def compare_to_no_action(session_returns: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare after-cost paper option returns with holding cash and avoiding options.

    Returns are fractions of the same total book equity per session. Every expected
    session must be present, including sessions with no option trade; those rows carry
    zero option gross return and zero option costs while the cash baseline keeps accruing.
    Directional accuracy is deliberately not an input to this economic comparison.
    """
    if not session_returns:
        raise NoActionError("complete session returns are required")
    raw_dates = [row.get("session") for row in session_returns]
    if any(not isinstance(day, str) or not day for day in raw_dates):
        raise NoActionError("each row needs a session key")
    dates = [str(day) for day in raw_dates]
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        raise NoActionError("session keys must be unique and chronological")

    option_equity = cash_equity = 1.0
    option_peak = cash_peak = 1.0
    option_drawdown = cash_drawdown = 0.0
    option_returns, cash_returns = [], []
    total_gross = total_cost = 0.0
    for row in session_returns:
        raw_gross = row.get("gross_return_on_book")
        raw_costs = row.get("all_in_cost_return_on_book")
        raw_cash_return = row.get("cash_return_on_book")
        if (not isinstance(raw_gross, (int, float)) or not isfinite(raw_gross)
                or not isinstance(raw_costs, (int, float)) or not isfinite(raw_costs)
                or not isinstance(raw_cash_return, (int, float)) or not isfinite(raw_cash_return)):
            raise NoActionError("gross, cost and cash returns must be finite fractions")
        gross, costs, cash_return = float(raw_gross), float(raw_costs), float(raw_cash_return)
        if costs < 0 or cash_return <= -1 or gross - costs <= -1:
            raise NoActionError("costs must be nonnegative and net returns above -100%")
        net = gross - costs
        total_gross += gross
        total_cost += costs
        option_returns.append(net)
        cash_returns.append(cash_return)
        option_equity *= 1 + net
        cash_equity *= 1 + cash_return
        option_peak = max(option_peak, option_equity)
        cash_peak = max(cash_peak, cash_equity)
        option_drawdown = min(option_drawdown, option_equity / option_peak - 1)
        cash_drawdown = min(cash_drawdown, cash_equity / cash_peak - 1)

    option_total = option_equity - 1
    cash_total = cash_equity - 1
    return {
        "schema": "fb.index_options_vs_no_action.v1",
        "sessions": len(session_returns),
        "option_gross_return_sum": total_gross,
        "all_in_cost_return_sum": total_cost,
        "option_net_cumulative_return": option_total,
        "cash_no_action_cumulative_return": cash_total,
        "net_excess_over_cash": option_total - cash_total,
        "option_max_drawdown": option_drawdown,
        "cash_max_drawdown": cash_drawdown,
        "option_sessions_positive_after_cost": sum(value > 0 for value in option_returns),
        "cash_sessions_positive": sum(value > 0 for value in cash_returns),
        "baseline": "hold_cash_avoid_options",
        "directional_accuracy_used_as_economic_evidence": False,
    }
