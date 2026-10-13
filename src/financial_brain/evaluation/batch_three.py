"""``PRE-REGISTERED edges batch three``: C1 rich-basis carry, N2F rejected results as a hedged futures short, EAP the
earnings-announcement premium in futures. Each returns per-event outcomes; ``monthly`` turns them into the period
series ADR-0004 judges, and ``event_null`` draws the G4 null (same dates, same counts, random members of the pool).
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from . import edges as E
from . import standard as S

STOCK_FUT_RT, NIFTY_RT = 0.0010, 0.0004
HEDGED_COST = STOCK_FUT_RT + NIFTY_RT
C1_THRESHOLD = 0.10
N2F_HOLD = 20
EAP_MIN, EAP_MAX = 3, 15
SCHEDULED = re.compile(r"scheduled\s+on\s+(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", re.I)


def monthly(ev: pd.DataFrame, col: str = "out") -> pd.Series:
    s = ev.groupby(ev["d"].dt.to_period("M"))[col].mean()
    s.index = s.index.to_timestamp()
    return s


def event_null(ev: pd.DataFrame, pool, *, direction: int = 1, draws: int = S.NULL_DRAWS, seed: int = 20261013) -> dict:
    """``pool(d, n, rng)`` returns ``n`` random outcomes available on date ``d`` (same construction as the events)."""
    rng = np.random.default_rng(seed)
    real = S.replication(monthly(ev), direction=direction)["stat"]
    counts = ev.groupby("d").size()
    sims = []
    for _ in range(draws):
        rows = []
        for d, n in counts.items():
            for x in pool(d, n, rng):
                rows.append((d, x))
        sim = pd.DataFrame(rows, columns=["d", "out"])
        sims.append(S.replication(monthly(sim), direction=direction)["stat"] if len(sim) else np.nan)
    sims = np.array([x for x in sims if np.isfinite(x)])
    q = float(np.quantile(sims, S.NULL_QUANTILE)) if len(sims) else float("nan")
    return {"stat": real, "null_q95": q, "null_mean": float(sims.mean()) if len(sims) else np.nan, "draws": len(sims),
            "pass": bool(np.isfinite(real) and np.isfinite(q) and real > q)}


# ------------------------------------------------------------------ shared panels

def fno_mask(basis: pd.DataFrame, index: pd.DatetimeIndex, symbols: list[str]) -> pd.DataFrame:
    """sessions x symbols: True where a near-month contract was live (start <= d <= expiry)."""
    M = pd.DataFrame(False, index=index, columns=symbols)
    b = basis[basis["symbol"].isin(symbols)]
    for sym, s, e in zip(b["symbol"], pd.to_datetime(b["start"]), pd.to_datetime(b["expiry"])):
        M.loc[(M.index >= s) & (M.index <= e), sym] = True
    return M


def panels(rcon, basis: pd.DataFrame) -> dict:
    """Spot closes of the F&O names (sessions x symbol), their membership, and the Nifty 50 price index."""
    P = E.load(rcon)
    tick = rcon.execute("""SELECT ticker, lineage, MAX(business_date) AS last FROM adjusted_prices
                           WHERE business_date >= '2015-06-01' GROUP BY 1, 2""").df()
    tick = tick.sort_values("last").drop_duplicates("ticker", keep="last")
    t2l = {t: l for t, l in zip(tick["ticker"], tick["lineage"]) if l in P["C"].columns}
    syms = sorted(set(basis["symbol"]) & set(t2l))
    C = P["C"][[t2l[s] for s in syms]].copy()
    C.columns = syms
    nifty = rcon.execute("""SELECT business_date, close_level FROM index_levels_canonical WHERE index_name = 'Nifty 50'
                            AND variant = 'PRICE' ORDER BY 1""").df()
    N = nifty.set_index(pd.to_datetime(nifty["business_date"]))["close_level"].reindex(C.index).ffill()
    return {"C": C, "M": fno_mask(basis, C.index, syms), "N": N, "P": P, "t2l": t2l}


def hedged_forward(C: pd.DataFrame, N: pd.Series, k: int, sign: int) -> pd.DataFrame:
    """sign x (stock return - Nifty return) over the next ``k`` sessions, net of the hedged round trip."""
    R = C.shift(-k) / C - 1
    NR = N.shift(-k) / N - 1
    return sign * R.sub(NR, axis=0) - HEDGED_COST


# ------------------------------------------------------------------ C1

def c1(basis: pd.DataFrame) -> tuple[pd.DataFrame, callable]:
    b = basis.copy()
    b["d"] = pd.to_datetime(b["start"])
    b["out"] = b["net"] - b["rf"]
    ev = b[b["basis0_pa"] > C1_THRESHOLD][["d", "symbol", "out"]].reset_index(drop=True)
    by_month = {m: g["out"].to_numpy() for m, g in b.groupby(b["d"].dt.to_period("M"))}

    def pool(d, n, rng):
        arr = by_month[pd.Timestamp(d).to_period("M")]
        return rng.choice(arr, size=min(n, len(arr)), replace=False)
    return ev, pool


# ------------------------------------------------------------------ N2F

def n2f(rcon, pan: dict, events: pd.DataFrame | None = None) -> tuple[pd.DataFrame, callable]:
    from . import momentum_book as MB
    events = MB.n2_events(rcon, pan["P"]) if events is None else events
    l2t = {lin: t for t, lin in pan["t2l"].items()}
    O = hedged_forward(pan["C"], pan["N"], N2F_HOLD, -1)
    rows = []
    for r in events[events["flag"]].itertuples():
        s = l2t.get(r.lineage)
        d = pd.Timestamp(r.after)
        if s not in pan["M"].columns or d not in O.index or not pan["M"].at[d, s]:
            continue
        x = O.at[d, s]
        if np.isfinite(x):
            rows.append({"d": d, "symbol": s, "out": float(x)})
    ev = pd.DataFrame(rows).drop_duplicates(["d", "symbol"])
    return ev, _pool(O, pan["M"])


def _pool(O: pd.DataFrame, M: pd.DataFrame):
    def pool(d, n, rng):
        row = O.loc[d][M.loc[d] & O.loc[d].notna()]
        return rng.choice(row.to_numpy(), size=min(n, len(row)), replace=False) if len(row) else []
    return pool


# ------------------------------------------------------------------ EAP

def eap(rcon, pan: dict) -> tuple[pd.DataFrame, callable]:
    a = rcon.execute("""SELECT DISTINCT a.business_date AS ann, a.headline, p.ticker FROM announcements a
                        JOIN (SELECT DISTINCT isin, ticker FROM adjusted_prices WHERE isin IS NOT NULL
                              AND ticker IN (SELECT UNNEST(?))) p USING (isin)
                        WHERE a.event_type = 'BOARD_MEETING' AND lower(a.headline) LIKE '%result%'
                          AND a.business_date >= '2015-06-01'""", [list(pan["C"].columns)]).df()
    idx = pan["C"].index
    rows = []
    for r in a.itertuples():
        m = SCHEDULED.search(r.headline or "")
        if not m:
            continue
        try:
            meet = pd.Timestamp(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            continue
        p0, pm = idx.searchsorted(pd.Timestamp(r.ann)), idx.searchsorted(meet)
        k = pm - p0
        if not (EAP_MIN <= k <= EAP_MAX) or pm + 1 >= len(idx):
            continue
        rows.append({"d": idx[p0], "symbol": r.ticker, "meet": meet, "k": int(k + 1)})
    ev = pd.DataFrame(rows).sort_values("d").drop_duplicates(["symbol", "meet"], keep="first")
    C, N, M = pan["C"], pan["N"], pan["M"]
    out = []
    pos = {d: i for i, d in enumerate(idx)}
    for r in ev.itertuples():
        if not M.at[r.d, r.symbol]:
            continue
        i = pos[r.d]
        c0, c1_ = C[r.symbol].iat[i], C[r.symbol].iat[i + r.k]
        n0, n1 = N.iat[i], N.iat[i + r.k]
        if all(np.isfinite([c0, c1_, n0, n1])):
            out.append({"d": r.d, "symbol": r.symbol, "k": r.k, "out": float(c1_ / c0 - 1 - (n1 / n0 - 1) - HEDGED_COST)})
    ev = pd.DataFrame(out)
    holds = ev.groupby("d")["k"].apply(list).to_dict()
    cache: dict[int, pd.DataFrame] = {}

    def pool(d, n, rng):
        ks = holds[d][:n]
        res = []
        for k in ks:
            if k not in cache:
                cache[k] = hedged_forward(C, N, k, +1)
            row = cache[k].loc[d][M.loc[d] & cache[k].loc[d].notna()]
            if len(row):
                res.append(float(rng.choice(row.to_numpy())))
        return res
    return ev, pool
