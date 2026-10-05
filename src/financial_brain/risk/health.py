"""A strategy's health, from its own record, by rules - never by a model's opinion.

    HEALTHY         a live record that is positive with confidence and behaving as claimed
    WARNING         no edge detected yet, or a symptom (execution costs, a weakening signal, under-delivering)
    REDUCE_CAPITAL  a drawdown or a degraded signal: keep running, at less size
    PAPER_ONLY      not enough live evidence, or untrustworthy data - every new strategy starts here
    DISABLED        losing with confidence, or a drawdown beyond the limit

The first rule that fires decides, in the order written in :func:`assess`, and every reason that applied is
returned, so the state is always explained. The thresholds are constants here, not tuned per strategy.
"""
from __future__ import annotations

from dataclasses import dataclass

HEALTHY, WARNING, REDUCE_CAPITAL, PAPER_ONLY, DISABLED = (
    "HEALTHY", "WARNING", "REDUCE_CAPITAL", "PAPER_ONLY", "DISABLED")
ORDER = {HEALTHY: 0, WARNING: 1, REDUCE_CAPITAL: 2, PAPER_ONLY: 3, DISABLED: 4}


@dataclass(frozen=True)
class Limits:
    min_trades: int = 100
    min_days: int = 20                 #: trades on one day share one market; confidence needs days, not trades
    disable_drawdown: float = 0.20
    reduce_drawdown: float = 0.10
    cost_warning: float = 2.0          #: realised execution cost / modelled
    cost_reduce: float = 3.0


def assess(*, trades: int, mean: float, se: float, drawdown: float = 0.0, expected: float | None = None,
           exec_cost: float | None = None, modelled_cost: float | None = None, data_ok: bool = True,
           signal_state: str | None = None, days: int | None = None, limits: Limits = Limits()) -> dict:
    """``mean`` and ``se``: the strategy's net result per trade (or per period) and its standard error, in the same
    units as ``expected`` and the costs."""
    reasons = []
    state = HEALTHY

    def worse(s, why):
        nonlocal state
        reasons.append(why)
        if ORDER[s] > ORDER[state]:
            state = s

    if not data_ok:
        worse(PAPER_ONLY, "data quality check failed")
    if trades < limits.min_trades:
        worse(PAPER_ONLY, f"{trades} live trades; {limits.min_trades} needed before capital")
    confident = days is None or days >= limits.min_days
    if not confident:
        reasons.append(f"{days} day(s) of record; {limits.min_days} needed before any verdict on the edge")
    if confident and trades >= 2 and se == se and mean + 2 * se < 0:
        worse(DISABLED, f"losing with confidence: {mean:+.4g} +- {2 * se:.4g}")
    if drawdown > limits.disable_drawdown:
        worse(DISABLED, f"drawdown {drawdown:.0%} beyond {limits.disable_drawdown:.0%}")
    elif drawdown > limits.reduce_drawdown:
        worse(REDUCE_CAPITAL, f"drawdown {drawdown:.0%} beyond {limits.reduce_drawdown:.0%}")
    if signal_state == "DEGRADED":
        worse(REDUCE_CAPITAL, "signal degraded (CUSUM and rolling t agree)")
    elif signal_state == "WATCH":
        worse(WARNING, "signal weakening (one detector fired)")
    if exec_cost is not None and modelled_cost:
        ratio = exec_cost / modelled_cost
        if ratio > limits.cost_reduce:
            worse(REDUCE_CAPITAL, f"execution costs {ratio:.1f}x the model")
        elif ratio > limits.cost_warning:
            worse(WARNING, f"execution costs {ratio:.1f}x the model")
    if confident and expected is not None and se == se and mean < expected - 2 * se:
        worse(WARNING, f"under-delivering its own claim: {mean:+.4g} vs expected {expected:+.4g}")
    if state == HEALTHY and not (confident and se == se and mean - 2 * se > 0):
        worse(WARNING, "no edge detected yet")
    return {"state": state, "reasons": reasons}
