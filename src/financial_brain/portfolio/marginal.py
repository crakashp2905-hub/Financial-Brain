"""Is this the best use of the next unit of risk? Ranking candidates by what they add to the book, not by alone.

A candidate's own alpha is not the question; what matters is how much expected return it adds **per unit of risk it
adds to the existing book**. A 2.8% idea uncorrelated with everything held can beat a 4.2% idea that is the same bet
as half the book. For a small step ``w`` into candidate i:

    added return        w * alpha_i
    added variance      2 w (S b)_i + w^2 S_ii           b = current weights, S = covariance
    added risk          sqrt(b'Sb + added variance) - sqrt(b'Sb)
    score               added return / added risk

With an empty book the score reduces to alpha_i / sigma_i (an information ratio). The covariance comes from
:func:`..portfolio.optimizer.shrunk_cov`, Ledoit-Wolf shrunk, because a sample covariance over a few hundred names
is nearly singular and would make every "uncorrelated" candidate look like a free lunch.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STEP = 0.02


class MarginalError(ValueError):
    pass


def scores(candidates: pd.DataFrame, cov: pd.DataFrame, book: dict[str, float] | None = None, *,
           step: float = STEP) -> pd.DataFrame:
    """``candidates``: ticker, alpha. ``cov``: covariance over (at least) candidates and held names, same units as
    alpha's horizon. ``book``: {ticker: weight}."""
    book = {k: v for k, v in (book or {}).items() if v}
    names = list(cov.index)
    missing = [t for t in list(candidates["ticker"]) + list(book) if t not in cov.index]
    if missing:
        raise MarginalError(f"no covariance for {missing[:5]}")
    S = cov.to_numpy(dtype=float)
    b = np.array([book.get(n, 0.0) for n in names])
    base_var = float(b @ S @ b)
    base_risk = np.sqrt(max(base_var, 0.0))
    Sb = S @ b
    idx = {n: i for i, n in enumerate(names)}
    rows = []
    for r in candidates.itertuples():
        i = idx[r.ticker]
        add_var = 2 * step * Sb[i] + step ** 2 * S[i, i]
        add_risk = np.sqrt(max(base_var + add_var, 0.0)) - base_risk
        own = np.sqrt(S[i, i])
        corr = float(Sb[i] / (own * base_risk)) if base_risk > 0 and own > 0 else float("nan")
        rows.append({"ticker": r.ticker, "alpha": float(r.alpha), "own_risk": float(own), "corr_to_book": corr,
                     "added_risk": float(add_risk),
                     "score": float(step * r.alpha / add_risk) if add_risk > 0 else float("inf") if r.alpha > 0 else 0.0})
    return pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)
