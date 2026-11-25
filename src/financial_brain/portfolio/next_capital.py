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


def allocate(candidates: pd.DataFrame, cov: pd.DataFrame, book_inr: dict, *, cash_return: float,
             budget: float = 100_000.0, step: float = 10_000.0, horizon: int = 60,
             max_weight: float = MAX_WEIGHT) -> dict:
    """Allocate on an explicit, horizon-matched return basis.

    Candidate net returns and ``cash_return`` must span the same horizon. Covariance is
    annualized. ``cash_return`` is mandatory so callers choose a sourced cash yield or an
    explicitly labeled zero-return control.
    """
    if not np.isfinite(cash_return):
        raise ValueError("cash_return must be finite and horizon-matched")
    names = list(cov.index)
    idx = {n: i for i, n in enumerate(names)}
    S = cov.to_numpy(dtype=float) * horizon / 252.0
    held = np.array([book_inr.get(n, 0.0) for n in names], dtype=float)
    total = held.sum() + budget
    alloc = np.zeros(len(names))
    cash = 0.0
    nets = dict(zip(candidates["ticker"], candidates["net"]))
    spent = 0.0
    candidate_return_total = 0.0
    risk_penalty_total = 0.0
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
            v = (float(net) - cash_return) * dw - RISK_AVERSION * add_var
            if v > best_v:
                best, best_v = t, v
                best_risk_penalty = RISK_AVERSION * add_var
        if best is None:
            cash += step
        else:
            alloc[idx[best]] += step
            candidate_return_total += float(nets[best]) * step
            risk_penalty_total += best_risk_penalty
        spent += step
    out = {n: float(alloc[idx[n]]) for n in names if alloc[idx[n]] > 0}
    retained_cash = cash + (budget - spent)
    invested = float(sum(out.values()))
    cash_opportunity_cost = cash_return * invested
    excess_vs_cash = candidate_return_total - cash_opportunity_cost
    return {
        "allocation": dict(sorted(out.items(), key=lambda kv: -kv[1])),
        "cash": retained_cash,
        "cash_return_hurdle": float(cash_return),
        "cash_counterfactual_return_inr": float(cash_return * budget),
        "cash_return_on_retained_cash_inr": float(cash_return * retained_cash),
        "cash_opportunity_cost_inr": float(cash_opportunity_cost),
        "candidate_expected_return_inr": float(candidate_return_total),
        "expected_excess_return_vs_all_cash_inr": float(excess_vs_cash),
        "risk_penalty_inr": float(risk_penalty_total),
        "portfolio_utility_vs_all_cash_inr": float(excess_vs_cash - risk_penalty_total),
    }


def compare(candidates: pd.DataFrame, cov: pd.DataFrame, book_inr: dict, *, cash_return: float,
            horizon: int = 60, budget: float = 100_000.0, step: float = 10_000.0,
            max_weight: float = MAX_WEIGHT) -> pd.DataFrame:
    """Rank candidates by initial marginal, risk-adjusted utility over cash."""
    if not np.isfinite(cash_return):
        raise ValueError("cash_return must be finite and horizon-matched")
    names = list(cov.index)
    S = cov.to_numpy(dtype=float) * horizon / 252.0
    held = np.array([book_inr.get(n, 0.0) for n in names], dtype=float)
    total = float(held.sum() + budget)
    w = held / total if total > 0 else held
    rows = []
    for r in candidates.itertuples():
        if r.ticker not in cov.index:
            continue
        i = names.index(r.ticker)
        vol = float(np.sqrt(S[i, i]))
        book_var = float(w @ S @ w)
        corr = float((S[i] @ w) / (vol * np.sqrt(book_var))) if book_var > 0 and vol > 0 else float("nan")
        dw = step / total if total > 0 else 0.0
        add_var = 2 * dw * (S[i] @ w) + dw * dw * S[i, i]
        add = np.sqrt(max(book_var + add_var, 0)) - np.sqrt(max(book_var, 0))
        can_add = (held[i] + step) / total <= max_weight if total > 0 else False
        utility = ((float(r.net) - cash_return) * dw - RISK_AVERSION * add_var
                   if can_add else float("-inf"))
        rows.append({"ticker": r.ticker, "net": r.net, "cash_return": cash_return,
                     "net_over_cash": float(r.net) - cash_return,
                     "clears_cash_hurdle": utility > 0, "marginal_utility_vs_cash": utility,
                     "vol": vol, "cost": getattr(r, "cost", np.nan),
                     "liquidity": getattr(r, "liquidity", None), "corr_to_book": corr,
                     "alpha_per_risk": (dw * (float(r.net) - cash_return)) / add
                     if add > 0 else float("inf") if utility > 0 else 0.0})
    columns = ["ticker", "net", "cash_return", "net_over_cash", "clears_cash_hurdle",
               "marginal_utility_vs_cash", "vol", "cost", "liquidity", "corr_to_book", "alpha_per_risk"]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows).sort_values("marginal_utility_vs_cash", ascending=False).reset_index(drop=True)


def reason(table: pd.DataFrame) -> str:
    if table.empty:
        return "no candidate to compare"
    highest_net = table.sort_values("net", ascending=False).iloc[0]
    eligible = table[table["clears_cash_hurdle"]]
    if eligible.empty:
        hurdle = float(table.iloc[0]["cash_return"])
        return (f"keep cash: no candidate clears the horizon-matched cash return hurdle "
                f"({hurdle * 100:+.2f}%) after marginal portfolio risk")
    table = eligible
    a = table.iloc[0]
    hi = highest_net
    why = f"choose {a['ticker']}: the highest expected alpha per unit of risk it adds to your book"
    if hi["ticker"] != a["ticker"]:
        why += (f" - {hi['ticker']} has more expected alpha ({hi['net'] * 100:+.2f}% vs {a['net'] * 100:+.2f}%) but "
                f"moves with what you hold (correlation {hi['corr_to_book']:+.2f} vs {a['corr_to_book']:+.2f})")
    return why
