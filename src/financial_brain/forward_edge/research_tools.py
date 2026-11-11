"""Leakage-safe research tools (FE-0079 .. FE-0090). Synthetic inputs in tests; nothing here claims alpha.

* Shadow opportunities are sealed before any outcome is attached; outcomes never rewrite them (FE-0079).
* Horizons are frozen at registration (FE-0080); tuning after forward data exists is a new trial (FE-0090).
* Net alpha is portfolio-level and after costs and financing (FE-0081); comparisons are paired against controls (FE-0082).
* Pattern uplift is measured after controlling for known factors (FE-0083).
* Abstention keeps a fixed denominator and counts missed winners (FE-0084).
* Negative and inconclusive cohorts stay discoverable forever (FE-0089).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from math import sqrt

import numpy as np


class ResearchError(ValueError):
    pass


# ---- FE-0079 shadow log ----

class ShadowLog:
    def __init__(self) -> None:
        self._opps: dict[tuple[str, str, str], dict] = {}
        self._outcomes: dict[tuple[str, str, str], float] = {}

    def seal(self, family: str, session: str, instrument: str, signal: str) -> None:
        key = (family, session, instrument)
        if key in self._opps:
            raise ResearchError("opportunity already sealed; it cannot be rewritten")
        self._opps[key] = {"signal": signal}

    def attach_outcome(self, family: str, session: str, instrument: str, outcome: float) -> None:
        key = (family, session, instrument)
        if key not in self._opps:
            raise ResearchError("an outcome needs a previously sealed opportunity")
        if key in self._outcomes:
            raise ResearchError("outcome already attached")
        self._outcomes[key] = outcome

    def opportunities(self, family: str) -> list[tuple[str, str, str]]:
        return sorted(k for k in self._opps if k[0] == family)

    def pending(self) -> list[tuple[str, str, str]]:
        return sorted(k for k in self._opps if k not in self._outcomes)


# ---- FE-0080 / FE-0090 frozen horizons and parameters ----

@dataclass(frozen=True)
class HorizonPlan:
    strategy_id: str
    horizon_sessions: int
    frozen_before_forward_data: bool

    def __post_init__(self) -> None:
        if self.horizon_sessions < 1:
            raise ResearchError("horizon must be at least one session")


def assert_horizon_unchanged(plan: HorizonPlan, proposed: int, *, forward_data_exists: bool) -> None:
    if proposed != plan.horizon_sessions and forward_data_exists:
        raise ResearchError("horizon cannot change after forward data exists: that is a new trial")


def assert_params_frozen(frozen: dict[str, str], proposed: dict[str, str], *, prospective_started: bool) -> None:
    if prospective_started and frozen != proposed:
        changed = sorted(k for k in set(frozen) | set(proposed) if frozen.get(k) != proposed.get(k))
        raise ResearchError(f"parameter change after prospective start is a new trial: {','.join(changed)}")


# ---- FE-0081 portfolio-level net alpha ----

def portfolio_net_alpha(*, equity_start: Decimal, gross_pnl: Decimal, costs: Decimal, financing: Decimal,
                        benchmark_return: Decimal, external_cash_flow: Decimal = Decimal(0)) -> dict[str, Decimal]:
    base = equity_start + external_cash_flow
    if base <= 0:
        raise ResearchError("equity base must be positive")
    net = gross_pnl - costs - financing
    ret = net / base
    return {"net_pnl": net, "net_return": ret, "net_alpha": ret - benchmark_return}


# ---- FE-0082 paired comparison ----

def paired_difference(strategy: list[float], control: list[float]) -> dict[str, float]:
    if len(strategy) != len(control) or not strategy:
        raise ResearchError("paired series must be the same non-zero length")
    diffs = [s - c for s, c in zip(strategy, control)]
    n = len(diffs)
    mean = sum(diffs) / n
    var = sum((d - mean) ** 2 for d in diffs) / (n - 1) if n > 1 else 0.0
    se = sqrt(var / n) if var > 0 else 0.0
    return {"n": float(n), "mean_diff": mean, "se": se, "t": mean / se if se > 0 else 0.0}


# ---- FE-0083 incremental uplift over known factors ----

def incremental_uplift(y: list[float], pattern: list[float], factors: list[list[float]]) -> dict[str, float]:
    """OLS of returns on [1, factors..., pattern]; returns the pattern coefficient and its naive t-stat."""
    yv = np.asarray(y, dtype=float)
    cols = [np.ones(len(yv))] + [np.asarray(f, dtype=float) for f in factors] + [np.asarray(pattern, dtype=float)]
    X = np.column_stack(cols)
    if X.shape[0] <= X.shape[1]:
        raise ResearchError("not enough observations for the controls")
    beta, *_ = np.linalg.lstsq(X, yv, rcond=None)
    resid = yv - X @ beta
    dof = X.shape[0] - X.shape[1]
    sigma2 = float(resid @ resid) / dof
    cov = sigma2 * np.linalg.inv(X.T @ X)
    se = float(np.sqrt(cov[-1, -1]))
    return {"pattern_beta": float(beta[-1]), "t": float(beta[-1]) / se if se > 0 else 0.0, "dof": float(dof)}


# ---- FE-0084 abstention accounting ----

def abstention_accounting(decisions: list[tuple[str, float | None]]) -> dict[str, float]:
    """``decisions`` = (action, hypothetical_return). Taken, abstained and the winners that abstention missed all stay
    in one fixed denominator."""
    taken = [r for a, r in decisions if a == "TAKE" and r is not None]
    abst = [r for a, r in decisions if a == "ABSTAIN" and r is not None]
    return {"total": float(len(decisions)), "taken": float(len(taken)), "abstained": float(len(abst)),
            "missed_winners": float(sum(1 for r in abst if r > 0)),
            "mean_taken": sum(taken) / len(taken) if taken else 0.0,
            "mean_abstained": sum(abst) / len(abst) if abst else 0.0}


# ---- FE-0085 .. FE-0087 mechanism-specific nets ----

def time_of_day_excess(strategy_returns: list[float], baseline_same_time_returns: list[float]) -> float:
    """Edge beyond the unconditional return for the same time-of-day and side."""
    if not strategy_returns or not baseline_same_time_returns:
        raise ResearchError("both series are required")
    return sum(strategy_returns) / len(strategy_returns) - sum(baseline_same_time_returns) / len(baseline_same_time_returns)


def futures_net_return(*, price_return: Decimal, spread_cost: Decimal, roll_cost: Decimal, funding_cost: Decimal) -> Decimal:
    return price_return - spread_cost - roll_cost - funding_cost


def spread_net_at_bid_ask(*, entry_credit_at_bid: Decimal | None, exit_debit_at_ask: Decimal | None,
                          quoted_mid_return: Decimal | None = None) -> tuple[str, Decimal | None]:
    """Executable P&L only. A mid-based return is never eligible."""
    if entry_credit_at_bid is None or exit_debit_at_ask is None:
        return "INELIGIBLE_NO_EXECUTABLE_QUOTE", None
    return "OK", entry_credit_at_bid - exit_debit_at_ask


# ---- FE-0089 archive ----

class CohortArchive:
    def __init__(self) -> None:
        self._rows: list[dict] = []

    def add(self, record: dict) -> int:
        if "cohort_id" not in record or "result" not in record:
            raise ResearchError("a record needs cohort_id and result")
        self._rows.append(dict(record))
        return len(self._rows)

    def all(self) -> list[dict]:
        return [dict(r) for r in self._rows]

    def not_positive(self) -> list[dict]:
        return [dict(r) for r in self._rows if r["result"] in ("NEGATIVE", "INCONCLUSIVE", "NO_SIGNAL")]
