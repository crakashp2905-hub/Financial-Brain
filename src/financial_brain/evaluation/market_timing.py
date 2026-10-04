"""Is being out of the market sometimes better than being in it?

Pre-registered in ``PRE-REGISTERED market timing and exit rules``. Each rule turns a daily series into a
0/1 "in the market" state. The state computed at the close of day t decides the position from the close of
t+1, so a rule can never trade on the return it has just seen. Out of the market, money earns a flat cash
rate; every change of state costs a one-way fee.

The question a timing rule must answer is not "did it avoid the crash", which any rule fitted to hindsight
does, but "is the risk-adjusted result *better than doing nothing*, by more than luck". That is judged with
a stationary block bootstrap (Politis & Romano 1994) on the daily return difference between the rule and
buy-and-hold, so volatility clustering and autocorrelation are preserved.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DAYS = 252
CASH_RATE = 0.06
ONE_WAY_COST = 0.0005


class TimingError(ValueError):
    pass


def trend(close: pd.Series, window: int = 200) -> pd.Series:
    return (close > close.rolling(window).mean()).where(close.rolling(window).mean().notna())


def cross(close: pd.Series, fast: int = 50, slow: int = 200) -> pd.Series:
    f, s = close.rolling(fast).mean(), close.rolling(slow).mean()
    return (f > s).where(s.notna())


def volatility(close: pd.Series, window: int = 20, mult: float = 1.5, base: int = 250) -> pd.Series:
    rv = close.pct_change().rolling(window).std()
    med = rv.rolling(base).median()
    return (rv <= mult * med).where(med.notna())


def breadth(pct_above: pd.Series, floor: float = 0.5) -> pd.Series:
    return (pct_above >= floor).where(pct_above.notna())


def run(close: pd.Series, state: pd.Series, *, cash_rate: float = CASH_RATE,
        one_way_cost: float = ONE_WAY_COST) -> pd.Series:
    """Daily strategy returns. ``state`` at t is applied to the return from t to t+1... shifted one day
    more: the position held over day t+1's return is decided at the close of day t."""
    r = close.pct_change()
    pos = state.reindex(close.index).astype(float).shift(1)
    cash = (1 + cash_rate) ** (1 / DAYS) - 1
    ret = pos * r + (1 - pos) * cash
    switches = pos.diff().abs().fillna(0.0)
    ret = ret - switches * one_way_cost
    return ret.where(pos.notna())


def stats(ret: pd.Series, *, cash_rate: float = CASH_RATE) -> dict:
    r = ret.dropna()
    if len(r) < 250:
        raise TimingError(f"{len(r)} observations is under a year")
    cash = (1 + cash_rate) ** (1 / DAYS) - 1
    eq = (1 + r).cumprod()
    years = len(r) / DAYS
    ex = r - cash
    dd = (eq / eq.cummax() - 1).min()
    cagr = eq.iloc[-1] ** (1 / years) - 1
    return {"cagr": float(cagr), "vol": float(r.std() * np.sqrt(DAYS)),
            "sharpe": float(ex.mean() / ex.std() * np.sqrt(DAYS)) if ex.std() > 0 else float("nan"),
            "max_dd": float(dd), "calmar": float(cagr / abs(dd)) if dd < 0 else float("nan"),
            "years": float(years)}


def block_bootstrap_sharpe_diff(a: pd.Series, b: pd.Series, *, reps: int = 5000, mean_block: int = 20,
                                seed: int = 0, cash_rate: float = CASH_RATE) -> dict:
    """Distribution of Sharpe(a) - Sharpe(b) under a stationary block bootstrap of the paired days."""
    d = pd.concat([a, b], axis=1).dropna()
    if len(d) < 500:
        raise TimingError("too short to bootstrap")
    x = d.to_numpy()
    n = len(x)
    cash = (1 + cash_rate) ** (1 / DAYS) - 1
    rng = np.random.default_rng(seed)
    p = 1.0 / mean_block
    diffs = np.empty(reps)
    pos = np.arange(n)
    for k in range(reps):
        # stationary bootstrap, vectorised: a new block starts with probability p at each step, a block
        # continues from its random start, wrapping at the end of the sample
        flags = rng.random(n) < p
        flags[0] = True
        block = np.cumsum(flags) - 1
        first = np.flatnonzero(flags)
        starts = rng.integers(n, size=len(first))
        idx = (starts[block] + (pos - first[block])) % n
        s = x[idx] - cash
        sa, sb = s[:, 0], s[:, 1]
        diffs[k] = (sa.mean() / sa.std() - sb.mean() / sb.std()) * np.sqrt(DAYS)
    return {"mean": float(diffs.mean()), "p01": float(np.percentile(diffs, 1)),
            "p05": float(np.percentile(diffs, 5)), "p95": float(np.percentile(diffs, 95)),
            "p99": float(np.percentile(diffs, 99)), "share_positive": float((diffs > 0).mean())}
