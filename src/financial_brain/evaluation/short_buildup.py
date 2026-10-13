"""F3, the short build-up avoid filter, exactly as ``PRE-REGISTERED short build-up avoid filter`` fixes it.

Every 20 sessions from 2019 (non-overlapping): among stocks with futures open interest, flag those whose spot fell
over the last 5 sessions while futures OI rose by more than the top-fifth cut. The outcome is the flagged names'
next-20-session spot return minus the equal-weighted F&O mean (gross: the use is to avoid). Judged with direction -1.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import edges as E
from . import standard as S

K, HOLD, Q = 5, 20, 0.80


def oi_panel(fut, S_: pd.DataFrame) -> pd.DataFrame:
    fd = fut.execute("SELECT symbol, d, oi FROM futures_daily WHERE oi > 0").df()
    fd["d"] = pd.to_datetime(fd["d"])
    OI = fd.pivot_table(index="d", columns="symbol", values="oi", aggfunc="sum").sort_index()
    return OI.reindex(index=S_.index, columns=S_.columns)


def flags(Sp: pd.DataFrame, OI: pd.DataFrame, M: pd.DataFrame, d) -> tuple[list, list]:
    """(flagged, eligible) at ``d``'s close."""
    r5 = Sp.loc[d] / Sp.shift(K).loc[d] - 1
    o5 = OI.loc[d] / OI.shift(K).loc[d] - 1
    ok = M.loc[d] & r5.notna() & o5.notna() & np.isfinite(o5)
    if ok.sum() < 50:
        return [], []
    cut = o5[ok].quantile(Q)
    flagged = list(o5[ok & (r5 < 0) & (o5 >= cut)].index)
    return flagged, list(ok[ok].index)


def run(rcon, fut, *, P: dict | None = None) -> dict:
    P = P or E.load(rcon)
    Sp, M = E.fno_panel(rcon, P, fut)
    OI = oi_panel(fut, Sp)
    start = OI.dropna(how="all").index[K] if OI.notna().any().any() else Sp.index[0]
    dates = E.every(Sp.index, HOLD, start=str(start.date()))
    F = (Sp.shift(-HOLD) / Sp - 1)
    books, eligible, bench = {}, {}, {}
    for d in dates:
        fl, el = flags(Sp, OI, M, d)
        el = [x for x in el if np.isfinite(F.at[d, x])]
        fl = [x for x in fl if x in set(el)]
        if len(fl) < 3 or len(el) < 50:
            continue
        books[d], eligible[d], bench[d] = fl, el, float(F.loc[d, el].mean())
    Fd = F.loc[list(books)]
    zero = pd.DataFrame(0.0, index=Fd.index, columns=Fd.columns)
    bench = pd.Series(bench)
    excess = S.book_series(Fd, zero, bench, books)              # equal weight, gross
    short_net = -excess - E.STOCK_FUT_RT
    return {"excess": excess, "short_net": short_net, "books": books, "eligible": eligible, "F": Fd, "cost": zero,
            "bench": bench, "n_flagged": float(np.mean([len(v) for v in books.values()])) if books else 0.0}
