"""Risk-adjusted performance, measured the same way for every strategy - and kept honest about luck.

Pre-registered in ``PRE-REGISTERED Sharpe ratio and Sharpe allocation``. From a series of periodic **net** returns:

    sharpe      annualised mean excess over the risk-free rate / annualised standard deviation
    sortino     the same over downside deviation only
    max_dd      the largest fall from a peak of the compounded series
    calmar      annualised return / max drawdown
    psr         probabilistic Sharpe ratio: P(true Sharpe > 0) given the sample's length, skew and kurtosis
    dsr         deflated Sharpe ratio: P(true Sharpe > the Sharpe the best of ``trials`` would show by luck)

PSR and DSR follow Bailey & Lopez de Prado (2012, 2014). They matter here because this project has tested hundreds of
variants: the best of 300 random strategies shows a respectable Sharpe by chance, and only the deflated number says
whether a strategy beats that.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

RISK_FREE = 0.065
EULER = 0.5772156649


def stats(r: pd.Series | np.ndarray, *, periods: int = 12, rf: float = RISK_FREE, trials: int = 1,
          trial_sharpe_sd: float | None = None) -> dict:
    """``r``: periodic net returns (fractions). ``periods``: periods a year (12 monthly, 252 daily, 52 weekly)."""
    x = np.asarray(pd.Series(r).dropna(), dtype=float)
    n = len(x)
    if n < 6:
        return {"n": n}
    ex = x - rf / periods
    sd = x.std(ddof=1)
    sr_p = ex.mean() / sd if sd > 0 else 0.0                          # per period
    down = np.sqrt(np.mean(np.minimum(ex, 0) ** 2))
    eq = np.cumprod(1 + x)
    dd = float((1 - eq / np.maximum.accumulate(eq)).max())
    ann = float(eq[-1] ** (periods / n) - 1)
    skew = float(((x - x.mean()) ** 3).mean() / x.std() ** 3) if x.std() > 0 else 0.0
    kurt = float(((x - x.mean()) ** 4).mean() / x.std() ** 4) if x.std() > 0 else 3.0
    out = {"n": n, "annual_return": ann, "annual_vol": float(sd * np.sqrt(periods)),
           "sharpe": float(sr_p * np.sqrt(periods)), "sortino": float(ex.mean() / down * np.sqrt(periods)) if down > 0 else float("nan"),
           "max_dd": dd, "calmar": ann / dd if dd > 0 else float("nan"), "skew": skew, "kurtosis": kurt,
           "psr": psr(sr_p, n, skew, kurt, 0.0)}
    if trials > 1:
        sd_sr = trial_sharpe_sd if trial_sharpe_sd is not None else 1.0 / np.sqrt(n)   # per-period SR spread across trials
        out["sharpe_luck"] = float(expected_max_sharpe(trials, sd_sr) * np.sqrt(periods))
        out["dsr"] = psr(sr_p, n, skew, kurt, expected_max_sharpe(trials, sd_sr))
    return out


def psr(sr: float, n: int, skew: float, kurt: float, benchmark: float) -> float:
    """P(true per-period Sharpe > ``benchmark``), Bailey & Lopez de Prado (2012)."""
    denom = np.sqrt(max(1 - skew * sr + (kurt - 1) / 4 * sr ** 2, 1e-12))
    return float(norm.cdf((sr - benchmark) * np.sqrt(n - 1) / denom))


def expected_max_sharpe(trials: int, sd: float) -> float:
    """The per-period Sharpe the best of ``trials`` zero-skill strategies shows by luck (the DSR benchmark)."""
    if trials <= 1:
        return 0.0
    return float(sd * ((1 - EULER) * norm.ppf(1 - 1 / trials) + EULER * norm.ppf(1 - 1 / (trials * np.e))))


def sharpe_allocation(panel: pd.DataFrame, *, lookback: int = 36, rf: float = RISK_FREE, turnover_cost: float = 0.003,
                      start=None) -> pd.DataFrame:
    """``panel``: months x strategies of net monthly returns. Each month, weights proportional to each strategy's
    trailing ``lookback``-month Sharpe (positive only; all cash if none), set from months strictly before it.
    Returns the allocation's monthly net return, equal weight's, and the weights."""
    rows, prev_w = [], pd.Series(0.0, index=panel.columns)
    for i, m in enumerate(panel.index):
        if start is not None and m < start:
            continue
        past = panel.iloc[max(0, i - lookback):i]
        avail = panel.columns[panel.iloc[i].notna() & (past.notna().sum() >= 12)]
        if len(avail) == 0:
            continue
        p = past[avail]
        sr = (p.mean() - rf / 12) / p.std(ddof=1)
        w = sr.clip(lower=0).fillna(0)
        w = w / w.sum() if w.sum() > 0 else w * 0
        w = w.reindex(panel.columns).fillna(0)
        cost = float((w - prev_w).abs().sum() * turnover_cost)
        r = panel.iloc[i].fillna(0)
        eq = r[avail].mean()
        rows.append({"month": m, "sharpe_alloc": float((w * r).sum() + (1 - w.sum()) * rf / 12 - cost),
                     "equal_weight": float(eq), "cash_share": float(1 - w.sum()), **{f"w_{k}": v for k, v in w.items()}})
        prev_w = w
    return pd.DataFrame(rows).set_index("month")
