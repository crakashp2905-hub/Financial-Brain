"""Where should the next unit of risk capital go? Capital allocation, not position sizing.

Position sizing asks "how much of this name". This asks: given what is already held, the candidates and their expected
net alpha, where does the next Rs 1 lakh do the most good - including the answer "keep some in cash"?

``allocate`` spends a budget in steps. Each step goes to the candidate with the highest **marginal value**:
expected net alpha on the step minus RISK_AVERSION x the variance it adds to the whole book (own variance and its
covariance with every holding and with what this allocation has already bought). A step whose best marginal value is
not positive stays in cash. Per-name caps keep any single name below MAX_WEIGHT of the book.

``compare`` is the table a trader reads before choosing between names, and ``reason`` says why the first one wins.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..decisions.net_alpha import RISK_AVERSION

MAX_WEIGHT = 0.10


def allocate(candidates: pd.DataFrame, cov: pd.DataFrame, book_inr: dict, *, budget: float = 100_000.0,
             step: float = 10_000.0, horizon: int = 60, max_weight: float = MAX_WEIGHT) -> dict:
    """``candidates``: ticker, net (expected net alpha over ``horizon``). ``cov``: annualised covariance over held and
    candidate names. ``book_inr``: current rupees per held name."""
    names = list(cov.index)
    idx = {n: i for i, n in enumerate(names)}
    S = cov.to_numpy(dtype=float) * horizon / 252.0
    held = np.array([book_inr.get(n, 0.0) for n in names], dtype=float)
    total = held.sum() + budget
    alloc = np.zeros(len(names))
    cash = 0.0
    nets = dict(zip(candidates["ticker"], candidates["net"]))
    spent = 0.0
    while spent + step <= budget + 1e-9:
        w = (held + alloc) / total
        best, best_v = None, 0.0
        for t, net in nets.items():
            if t not in idx:
                continue
            i = idx[t]
            if (held[i] + alloc[i] + step) / total > max_weight:
                continue
            dw = step / total
            add_var = 2 * dw * (S[i] @ w) + dw * dw * S[i, i]
            v = net * dw - RISK_AVERSION * add_var
            if v > best_v:
                best, best_v = t, v
        if best is None:
            cash += step
        else:
            alloc[idx[best]] += step
        spent += step
    out = {n: float(alloc[idx[n]]) for n in names if alloc[idx[n]] > 0}
    return {"allocation": dict(sorted(out.items(), key=lambda kv: -kv[1])), "cash": cash + (budget - spent)}


def compare(candidates: pd.DataFrame, cov: pd.DataFrame, book_inr: dict, *, horizon: int = 60) -> pd.DataFrame:
    """Per candidate: expected alpha, volatility, cost, liquidity, correlation with the book, and alpha per unit of
    risk added to the book (the number that decides)."""
    names = list(cov.index)
    S = cov.to_numpy(dtype=float)
    held = np.array([book_inr.get(n, 0.0) for n in names], dtype=float)
    w = held / held.sum() if held.sum() > 0 else held
    book_var = float(w @ S @ w)
    rows = []
    for r in candidates.itertuples():
        if r.ticker not in cov.index:
            continue
        i = names.index(r.ticker)
        vol = float(np.sqrt(S[i, i]))
        corr = float((S[i] @ w) / (vol * np.sqrt(book_var))) if book_var > 0 and vol > 0 else float("nan")
        dw = 0.02
        add = np.sqrt(max(book_var + 2 * dw * (S[i] @ w) + dw * dw * S[i, i], 0)) - np.sqrt(max(book_var, 0))
        rows.append({"ticker": r.ticker, "net": r.net, "vol": vol, "cost": getattr(r, "cost", np.nan),
                     "liquidity": getattr(r, "liquidity", None), "corr_to_book": corr,
                     "alpha_per_risk": (dw * r.net) / add if add > 0 else float("inf")})
    return pd.DataFrame(rows).sort_values("alpha_per_risk", ascending=False).reset_index(drop=True)


def reason(table: pd.DataFrame) -> str:
    if table.empty:
        return "no candidate to compare"
    a = table.iloc[0]
    hi = table.sort_values("net", ascending=False).iloc[0]
    why = f"choose {a['ticker']}: the highest expected alpha per unit of risk it adds to your book"
    if hi["ticker"] != a["ticker"]:
        why += (f" - {hi['ticker']} has more expected alpha ({hi['net'] * 100:+.2f}% vs {a['net'] * 100:+.2f}%) but "
                f"moves with what you hold (correlation {hi['corr_to_book']:+.2f} vs {a['corr_to_book']:+.2f})")
    return why
