"""Betting against beta in stock futures, exactly as ``PRE-REGISTERED betting against beta in futures`` fixes it.

Monthly: shrunk 252-session beta to the Nifty (0.6 x time-series + 0.4), bottom third long and top third short,
equal weight within a leg, legs scaled to 1/beta so the book's ex-ante beta is zero; each leg earns spot minus the
risk-free rate; 5 bp one way on every unit of levered notional changed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import standard as S

RF = 0.065
ONE_WAY = 0.0005
WINDOW = 252
SHRINK = 0.6


def betas(C: pd.DataFrame, N: pd.Series, d: pd.Timestamp) -> pd.Series:
    i = C.index.get_loc(d)
    if i < WINDOW:
        return pd.Series(dtype=float)
    r = C.iloc[i - WINDOW:i + 1].pct_change(fill_method=None).iloc[1:]
    m = N.iloc[i - WINDOW:i + 1].pct_change().iloc[1:]
    ok = r.notna().sum() >= int(WINDOW * 0.8)
    r = r.loc[:, ok]
    mv = m.var()
    cov = r.apply(lambda x: x.cov(m))
    return SHRINK * cov / mv + (1 - SHRINK) * 1.0


def month_starts(index: pd.DatetimeIndex, start: str = "2016-02-01") -> list:
    s = pd.Series(index, index=index)
    firsts = s.groupby(index.to_period("M")).min()
    return [d for d in firsts if d >= pd.Timestamp(start)]


def book_return(C, d, nxt, longs, shorts, b: pd.Series, prev_w: pd.Series) -> tuple[float, float, pd.Series]:
    bl, bh = float(b[longs].mean()), float(b[shorts].mean())
    w = pd.concat([pd.Series(1.0 / len(longs) / bl, index=longs), pd.Series(-1.0 / len(shorts) / bh, index=shorts)])
    w = w.groupby(level=0).sum()
    r = C.loc[nxt, w.index] / C.loc[d, w.index] - 1
    rf = RF * (nxt - d).days / 365
    long_part = float((w[w > 0] * (r[w > 0] - rf)).sum())
    short_part = float((w[w < 0] * (r[w < 0] - rf)).sum())
    cost = float(w.sub(prev_w, fill_value=0).abs().sum() * ONE_WAY)
    return long_part + short_part - cost, cost, w


def run(pan: dict, *, legs_fn=None, cache: dict | None = None) -> pd.DataFrame:
    """``legs_fn(d, eligible_sorted_by_beta) -> (longs, shorts)``; default: bottom and top thirds."""
    C, M, N = pan["C"], pan["M"], pan["N"]
    dates = month_starts(C.index)
    rows, prev = [], pd.Series(dtype=float)
    for d, nxt in zip(dates, dates[1:]):
        b = cache[d] if cache is not None and d in cache else betas(C, N, d)
        if cache is not None:
            cache[d] = b
        el = [x for x in b.index if M.at[d, x] and np.isfinite(C.at[nxt, x]) and np.isfinite(C.at[d, x])
              and np.isfinite(b[x]) and b[x] > 0]
        if len(el) < 30:
            continue
        srt = b[el].sort_values()
        n = len(srt) // 3
        longs, shorts = (legs_fn(d, srt) if legs_fn else (list(srt.index[:n]), list(srt.index[-n:])))
        net, cost, prev = book_return(C, d, nxt, longs, shorts, b, prev)
        rows.append({"d": d, "net": net, "cost": cost, "beta_l": float(b[longs].mean()), "beta_h": float(b[shorts].mean()),
                     "n": n})
    return pd.DataFrame(rows).set_index("d")


def null(pan: dict, real: pd.Series, *, draws: int = S.NULL_DRAWS, seed: int = 20261013, cache: dict | None = None) -> dict:
    stat = S.replication(real)["stat"]
    sims = []
    cache = {} if cache is None else cache
    for i in range(draws):
        rng = np.random.default_rng(seed + i)

        def legs(d, srt, rng=rng):
            n = len(srt) // 3
            pick = rng.permutation(list(srt.index))
            return list(pick[:n]), list(pick[n:2 * n])
        sims.append(S.replication(run(pan, legs_fn=legs, cache=cache)["net"])["stat"])
    sims = np.array([x for x in sims if np.isfinite(x)])
    q = float(np.quantile(sims, S.NULL_QUANTILE))
    return {"stat": stat, "null_q95": q, "null_mean": float(sims.mean()), "draws": len(sims),
            "pass": bool(np.isfinite(stat) and stat > q)}
