"""Family-level validation: is the best of several tested strategies better than luck, and does choosing the best
in-sample predict anything out of sample?

Two tests the public validation toolkits use and this project lacked:

* **White's Reality Check (2000) / Hansen's SPA (2005).** Given K strategies' period returns in excess of a
  benchmark (here: zero, because each series is already an excess or a timing alpha), is the *best* mean excess
  positive once the search over K is accounted for? Stationary bootstrap (Politis & Romano 1994) of the periods,
  recentred. RC uses raw means; SPA studentises each strategy and recentres only the ones that are not hopeless, which
  makes it less sensitive to adding bad strategies to the family.

* **Probability of backtest overfitting** (Bailey, Borwein, Lopez de Prado & Zhu 2017), by combinatorially
  symmetric cross-validation: split the T periods into S blocks; for every way of choosing S/2 blocks as in-sample,
  pick the strategy with the best in-sample Sharpe and record where it ranks out of sample. PBO is the share of
  splits in which the in-sample winner ranks below the out-of-sample median. 0.5 is what choosing at random gives.
"""
from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd


def stationary_indices(n: int, mean_block: float, rng: np.random.Generator) -> np.ndarray:
    p = 1.0 / mean_block
    idx = np.empty(n, dtype=int)
    idx[0] = rng.integers(n)
    for t in range(1, n):
        idx[t] = rng.integers(n) if rng.random() < p else (idx[t - 1] + 1) % n
    return idx


def reality_check(X: pd.DataFrame, *, reps: int = 2000, mean_block: float = 6.0, seed: int = 20261014) -> dict:
    """``X``: periods x strategies of excess returns (benchmark already subtracted). Missing periods are dropped
    per column by aligning on rows where every strategy has a value."""
    X = X.dropna()
    n, k = X.shape
    A = X.to_numpy()
    mean = A.mean(axis=0)
    sd = A.std(axis=0, ddof=1)
    sd[sd == 0] = np.nan
    rng = np.random.default_rng(seed)
    rc_stat = np.sqrt(n) * mean.max()
    t = np.sqrt(n) * mean / sd
    spa_stat = max(np.nanmax(t), 0.0)
    # SPA's recentring: strategies far below zero are not recentred (Hansen's consistent version)
    keep = mean >= -sd * np.sqrt(2 * np.log(np.log(n)) / n)
    centre = np.where(keep, mean, 0.0)
    rc_b, spa_b = np.empty(reps), np.empty(reps)
    for b in range(reps):
        Ab = A[stationary_indices(n, mean_block, rng)]
        mb = Ab.mean(axis=0)
        rc_b[b] = np.sqrt(n) * (mb - mean).max()
        spa_b[b] = max(np.nanmax(np.sqrt(n) * (mb - centre) / sd), 0.0)
    best = int(np.nanargmax(t))
    return {"strategies": k, "periods": n, "best": X.columns[best], "best_t": float(t[best]),
            "rc_p": float((rc_b >= rc_stat).mean()), "spa_p": float((spa_b >= spa_stat).mean())}


def pbo(X: pd.DataFrame, *, blocks: int = 16) -> dict:
    """CSCV over ``blocks`` contiguous blocks of the aligned periods (an even number)."""
    X = X.dropna()
    A = X.to_numpy()
    n, k = A.shape
    if blocks % 2 or n < blocks * 2 or k < 2:
        raise ValueError("need an even number of blocks, at least two periods a block and two strategies")
    parts = np.array_split(np.arange(n), blocks)
    lams, degr = [], []

    def sharpe(M):
        s = M.std(axis=0, ddof=1)
        out = np.full_like(s, -np.inf, dtype=float)
        np.divide(M.mean(axis=0), s, out=out, where=s > 0)
        return out

    for ins in combinations(range(blocks), blocks // 2):
        i_idx = np.concatenate([parts[j] for j in ins])
        o_idx = np.concatenate([parts[j] for j in range(blocks) if j not in ins])
        s_in, s_out = sharpe(A[i_idx]), sharpe(A[o_idx])
        star = int(np.argmax(s_in))
        rank = (s_out < s_out[star]).sum() + 0.5 * ((s_out == s_out[star]).sum() - 1) + 1   # 1..k
        w = rank / (k + 1)
        lams.append(np.log(w / (1 - w)))
        degr.append((s_in[star], s_out[star]))
    lams = np.array(lams)
    d = np.array(degr)
    slope = float(np.polyfit(d[:, 0], d[:, 1], 1)[0]) if len(d) > 2 else float("nan")
    return {"pbo": float((lams <= 0).mean()), "splits": len(lams), "strategies": k, "periods": n,
            "is_to_oos_slope": slope}
