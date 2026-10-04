"""A point-in-time fundamentals panel: what could be computed about a company on a given session.

Pre-registered in ``PRE-REGISTERED fundamentals factors``. For each (lineage, session) it uses only the
filings the exchange had **disseminated on or before that session**, and where a period was filed twice
(a restatement) it uses the latest version known *then*, never the later one.

    ep          TTM profit after tax / market cap        market cap = close_raw x paid-up capital / face value
    sue         (EPS_q - EPS_q-4) / sd of that change over the previous quarters (min 4)
    margin      TTM profit after tax / TTM revenue
    rev_growth  TTM revenue / TTM revenue a year earlier - 1

Quarters must be **consecutive**: a TTM sum over a gap (a quarter the company never filed, or one the
archive missed) would be a sum over the wrong window, so a window with a hole yields no value rather
than a plausible wrong one. The same applies to the year-earlier quarter behind ``sue`` and
``rev_growth``.

Market cap uses the *raw* close of the session with the share count from the latest filing known then.
Across a split the two can disagree for up to a quarter; values with ``|ep| > 1`` are dropped as that
kind of artifact rather than trusted.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FACTORS = ("ep", "sue", "margin", "rev_growth")
Q_DAYS = (75, 110)         #: acceptable spacing between consecutive quarter-ends
Y_DAYS = (350, 380)
MIN_PRIOR_DIFFS = 4
EP_LIMIT = 1.0


class FundamentalsError(ValueError):
    pass


def known_quarters(fr: pd.DataFrame, on) -> pd.DataFrame:
    """One row per period_end as it was known on ``on``: the latest filing at or before that moment."""
    k = fr[fr["filed_at"] <= pd.Timestamp(on)]
    if k.empty:
        return k
    k = k.sort_values(["period_end", "filed_at"]).groupby("period_end", as_index=False).last()
    return k.sort_values("period_end", ascending=False).reset_index(drop=True)


def _consecutive(ends: pd.Series, n: int, gap=Q_DAYS) -> bool:
    d = ends.iloc[:n].reset_index(drop=True)
    if len(d) < n:
        return False
    steps = [(d[i] - d[i + 1]).days for i in range(n - 1)]
    return all(gap[0] <= s <= gap[1] for s in steps)


def factors_for(q: pd.DataFrame, *, price: float | None, shares: float | None) -> dict:
    """The four factors from one company's known quarters (newest first). Missing pieces give NaN."""
    out = {f: np.nan for f in FACTORS}
    if len(q) < 4 or not _consecutive(q["period_end"], 4):
        return out
    ttm_pat, ttm_rev = q["pat"].iloc[:4].sum(), q["revenue"].iloc[:4].sum()
    if np.isfinite(ttm_rev) and ttm_rev > 0 and np.isfinite(ttm_pat):
        out["margin"] = float(ttm_pat / ttm_rev)
    if price and shares and price > 0 and shares > 0 and np.isfinite(ttm_pat):
        ep = ttm_pat / (price * shares)
        if abs(ep) <= EP_LIMIT:
            out["ep"] = float(ep)
    if len(q) >= 8 and _consecutive(q["period_end"], 8):
        prev = q["revenue"].iloc[4:8].sum()
        if prev > 0 and np.isfinite(ttm_rev):
            out["rev_growth"] = float(ttm_rev / prev - 1)
    # sue: needs the same quarter a year earlier for every diff, so index k pairs with k+4
    eps = q["eps"].to_numpy(dtype=float)
    if len(q) >= 4 + 1 + MIN_PRIOR_DIFFS and _consecutive(q["period_end"], min(len(q), 10)):
        n_d = min(len(q) - 4, 7)
        diffs = eps[:n_d] - eps[4:4 + n_d]
        prior = diffs[1:][np.isfinite(diffs[1:])]
        if np.isfinite(diffs[0]) and len(prior) >= MIN_PRIOR_DIFFS and prior.std(ddof=1) > 0:
            out["sue"] = float(diffs[0] / prior.std(ddof=1))
    return out


def build(fr: pd.DataFrame, shares: pd.DataFrame, prices: pd.DataFrame, sessions, *,
          basis_pref: str = "consolidated") -> pd.DataFrame:
    """The panel. ``fr``: isin, period_end, filed_at, basis, revenue, pat, eps. ``shares``: isin, filed_at,
    shares. ``prices``: isin, business_date, close_raw. Returns (isin, business_date, factors)."""
    if fr.empty:
        raise FundamentalsError("no filings to build from")
    # one basis per company: the preferred one if it has at least 8 quarters, else the other
    count = fr.groupby(["isin", "basis"]).period_end.nunique().unstack(fill_value=0)
    use = {}
    for isin, row in count.iterrows():
        other = "standalone" if basis_pref == "consolidated" else "consolidated"
        use[isin] = basis_pref if row.get(basis_pref, 0) >= 8 else other if row.get(other, 0) > 0 else basis_pref
    fr = fr[fr["basis"] == fr["isin"].map(use)]
    px = prices.set_index(["isin", "business_date"])["close_raw"]
    rows = []
    for s in sessions:
        s = pd.Timestamp(s)
        sh = shares[shares["filed_at"] <= s].sort_values("filed_at").groupby("isin")["shares"].last()
        for isin, g in fr[fr["filed_at"] <= s].groupby("isin"):
            q = known_quarters(g, s)
            try:
                price = float(px.loc[(isin, s)])
            except KeyError:
                price = None
            f = factors_for(q, price=price, shares=float(sh.get(isin, np.nan)) if isin in sh.index else None)
            if any(np.isfinite(v) for v in f.values()):
                rows.append({"isin": isin, "business_date": s, **f})
    return pd.DataFrame(rows)
