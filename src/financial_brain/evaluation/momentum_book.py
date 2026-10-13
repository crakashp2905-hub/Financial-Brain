"""M1, the cost-aware momentum book, exactly as ``PRE-REGISTERED cost-aware momentum book`` fixes it.

    universe    the 500 most liquid names each rebalance (``edges.load``)
    rebalance   every 40 sessions from the first session of 2016
    score       mean percentile rank of 12-1 momentum and nearness to the 52-week high
    exclusion   the live N2 flag: a results reaction in the bottom fifth of the previous 30 days' reactions,
                for 20 sessions from the close that completed it
    holdings    keep while ranked in the top 100 of eligible names, fill to 40 from the top
    weights     inverse 60-session volatility, capped at 5%
    costs       per-name delivery round trip by liquidity bucket, half on each unit of weight changed

``targets`` gives the same book for one day, for the paper zoo; ``run`` gives the backtest the registration judges.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import edges as E
from . import standard as S

HOLD = 40
ENTER, KEEP = 40, 100
CAP = 0.05
N2_SESSIONS, N2_DAYS, N2_Q, N2_MIN_PEERS = 20, 30, 0.20, 5


def scores(C: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    mom = C.shift(21) / C.shift(252) - 1
    near = C / C.rolling(252, min_periods=252).max()
    return mom, near


def rank_score(mom: pd.Series, near: pd.Series) -> pd.Series:
    ok = mom.notna() & near.notna()
    return (mom[ok].rank(pct=True) + near[ok].rank(pct=True)) / 2


def n2_events(rcon, P: dict) -> pd.DataFrame:
    """Every RESULTS reaction (close before -> close of the session after, minus the equal-weighted universe) with
    its point-in-time percentile against the reactions completed in the previous 30 days."""
    C, U = P["C"], P["U"]
    ev = rcon.execute("""SELECT DISTINCT a.business_date AS d, p.lineage FROM announcements a
                         JOIN (SELECT DISTINCT isin, lineage FROM adjusted_prices WHERE isin IS NOT NULL) p USING (isin)
                         WHERE a.event_type = 'RESULTS' AND a.business_date >= '2015-01-01'""").df()
    ev["d"] = pd.to_datetime(ev["d"])
    ev = ev[ev["lineage"].isin(C.columns)]
    idx = C.index
    p0 = idx.searchsorted(ev["d"])
    ev = ev.assign(p0=p0)
    ev = ev[(ev["p0"] >= 1) & (ev["p0"] + 1 < len(idx))]
    ew = C.pct_change(fill_method=None).where(U).mean(axis=1)
    cum = (1 + ew.fillna(0)).cumprod()
    before, after = idx[ev["p0"].to_numpy() - 1], idx[ev["p0"].to_numpy() + 1]
    col = C.columns.get_indexer(ev["lineage"])
    cv = C.to_numpy()
    c0 = cv[idx.get_indexer(before), col]
    c1 = cv[idx.get_indexer(after), col]
    react = c1 / c0 - 1 - (cum.loc[after].to_numpy() / cum.loc[before].to_numpy() - 1)
    out = pd.DataFrame({"lineage": ev["lineage"].to_numpy(), "after": after, "react": react})
    out = out[np.isfinite(out["react"])].drop_duplicates(["lineage", "after"]).sort_values("after").reset_index(drop=True)
    pct = np.full(len(out), np.nan)
    npeer = np.zeros(len(out), dtype=int)
    a = out["after"].to_numpy()
    r = out["react"].to_numpy()
    for day in np.unique(a):
        win = (a <= day) & (a > day - np.timedelta64(N2_DAYS, "D"))
        peers = np.sort(r[win])
        here = np.flatnonzero(a == day)
        lo = np.searchsorted(peers, r[here], side="left")
        hi = np.searchsorted(peers, r[here], side="right")
        pct[here] = (lo + 0.5 * (hi - lo)) / len(peers)          # mid-rank, as news_filter.flagged
        npeer[here] = len(peers)
    out["pct"], out["peers"] = pct, npeer
    out["flag"] = (out["pct"] <= N2_Q) & (out["peers"] >= N2_MIN_PEERS)
    return out


def n2_flagged(events: pd.DataFrame, idx: pd.DatetimeIndex, d: pd.Timestamp) -> set:
    """Names flagged at ``d``'s close: a flagged reaction completed within the last 20 sessions."""
    pos = idx.get_loc(d)
    start = idx[max(pos - N2_SESSIONS + 1, 0)]
    f = events[events["flag"] & (events["after"] >= start) & (events["after"] <= d)]
    return set(f["lineage"])


def inverse_vol(vol: pd.Series, cap: float = CAP) -> pd.Series:
    w = (1 / vol.replace(0, np.nan)).dropna()
    if w.empty:
        return w
    w = w / w.sum()
    for _ in range(20):                                     # cap, then spread the excess pro rata
        over = w > cap
        if not over.any():
            break
        excess = float((w[over] - cap).sum())
        w[over] = cap
        under = ~over
        w[under] += excess * w[under] / w[under].sum()
    return w / w.sum()


def choose(score: pd.Series, held: list, enter: int = ENTER, keep: int = KEEP) -> list:
    """Keep held names ranked in the top ``keep``; fill to ``enter`` from the top of the ranking."""
    rank = score.rank(ascending=False, method="first")
    stay = [x for x in held if x in rank.index and rank[x] <= keep]
    for x in score.sort_values(ascending=False).index:
        if len(stay) >= enter:
            break
        if x not in stay:
            stay.append(x)
    return stay[:max(enter, len(stay))]


def run(rcon, *, exclude_n2: bool = True, P: dict | None = None, events: pd.DataFrame | None = None) -> dict:
    """The registered backtest. Returns the period table, the books, and what the null needs."""
    P = P or E.load(rcon)
    C, U, cost = P["C"], P["U"], P["cost"]
    mom, near = scores(C)
    vol = C.pct_change(fill_method=None).rolling(60, min_periods=60).std()
    events = n2_events(rcon, P) if (exclude_n2 and events is None) else events
    dates = E.every(C.index, HOLD, start="2016-01-01")
    books, eligible, held = {}, {}, []
    F = pd.DataFrame(np.nan, index=dates, columns=C.columns)
    bench = {}
    for d, nxt in zip(dates, dates[1:]):
        uni = U.loc[d][U.loc[d]].index
        sc = rank_score(mom.loc[d, uni], near.loc[d, uni])
        sc = sc[vol.loc[d, sc.index].notna()]
        flagged = n2_flagged(events, C.index, d) if exclude_n2 else set()
        sc = sc[[x for x in sc.index if x not in flagged]]
        held = choose(sc, held)
        books[d], eligible[d] = held, list(sc.index)
        F.loc[d] = C.loc[nxt] / C.loc[d] - 1
        bench[d] = float(F.loc[d, uni].mean())
    F = F.loc[list(books)]
    bench = pd.Series(bench)

    def weigh(d, names):
        return inverse_vol(vol.loc[d, names]).reindex(names).fillna(0.0)

    net = S.book_series(F, cost.reindex(F.index), bench, books, weigh)
    turn = []
    for a, b in zip(list(books), list(books)[1:]):
        turn.append(len(set(books[b]) - set(books[a])) / max(len(books[b]), 1))
    return {"net": net, "books": books, "eligible": eligible, "F": F, "cost": cost.reindex(F.index), "bench": bench,
            "weigh": weigh, "turnover": float(np.mean(turn)) if turn else float("nan"), "P": P, "events": events}


def targets(rcon, day, held: list) -> tuple[dict, list]:
    """The book for ``day``'s close: target weights (lineage -> weight) and the names held, given yesterday's."""
    from datetime import timedelta
    P = E.load(rcon, start=str(pd.Timestamp(day) - timedelta(days=600))[:10])
    P = {k: v.loc[: pd.Timestamp(day)] for k, v in P.items()}
    C, U = P["C"], P["U"]
    d = C.index[-1]
    mom, near = scores(C)
    vol = C.pct_change(fill_method=None).rolling(60, min_periods=60).std()
    uni = U.loc[d][U.loc[d]].index
    sc = rank_score(mom.loc[d, uni], near.loc[d, uni])
    sc = sc[vol.loc[d, sc.index].notna()]
    from ..decisions import news_filter as NF
    flagged = set(NF.flagged(rcon, d.date()))
    sc = sc[[x for x in sc.index if x not in flagged]]
    book = choose(sc, list(held))
    w = inverse_vol(vol.loc[d, book])
    return {k: float(v) for k, v in w.items()}, book
