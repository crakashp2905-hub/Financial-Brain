"""Trial 8, ``PRE-REGISTERED delivery share``: F&O names, score = mean delivery % over the last 20 sessions minus the
mean over the 120 before; every 20 sessions long the top fifth and short the bottom fifth, equal weight, 5 bp one way
(``edges.long_short``, the harness every futures long-short here used)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import edges as E
from . import standard as S

RECENT, BASE = 20, 120
MIN_RECENT, MIN_BASE = 15, 90
FRAC, ONE_WAY, HOLD = 0.20, 0.0005, 20


def delivery_panel(rcon, symbols_to_lineage: dict[str, str], index: pd.DatetimeIndex) -> pd.DataFrame:
    """sessions x symbol: delivery %, joined through the ticker each session carried (renames handled)."""
    d = rcon.execute("""SELECT p.lineage, d.business_date, d.deliv_pct FROM delivery d
                        JOIN adjusted_prices p ON p.ticker = d.symbol AND p.business_date = d.business_date
                        WHERE d.series = 'EQ' AND p.lineage IN (SELECT UNNEST(?))""",
                     [list(symbols_to_lineage.values())]).df()
    d["business_date"] = pd.to_datetime(d["business_date"])
    P = d.pivot_table(index="business_date", columns="lineage", values="deliv_pct").reindex(index)
    l2s = {lin: s for s, lin in symbols_to_lineage.items()}
    return P.rename(columns=l2s)


def score(D: pd.DataFrame) -> pd.DataFrame:
    recent = D.rolling(RECENT, min_periods=MIN_RECENT).mean()
    base = D.shift(RECENT).rolling(BASE, min_periods=MIN_BASE).mean()
    return recent - base


def run(pan: dict, D: pd.DataFrame, *, scores: pd.DataFrame | None = None) -> pd.DataFrame:
    sc = score(D) if scores is None else scores
    sc = sc.reindex(columns=pan["C"].columns)
    first = sc.dropna(how="all").index.min()
    dates = E.every(pan["C"].index, HOLD, start=str(max(first, pd.Timestamp("2016-01-01")).date()))
    return E.long_short(pan["C"], pan["M"], sc, dates, frac=FRAC, one_way=ONE_WAY)


def null(pan: dict, D: pd.DataFrame, real: pd.Series, *, draws: int = S.NULL_DRAWS, seed: int = 20261013) -> dict:
    """Random legs of the same sizes: the scores shuffled across names within each session."""
    stat = S.replication(real)["stat"]
    base = score(D).reindex(columns=pan["C"].columns)
    sims = []
    for i in range(draws):
        rng = np.random.default_rng(seed + i)
        arr = base.to_numpy().copy()
        for r in range(arr.shape[0]):
            ok = np.isfinite(arr[r])
            arr[r, ok] = rng.permutation(arr[r, ok])
        sims.append(S.replication(run(pan, D, scores=pd.DataFrame(arr, index=base.index, columns=base.columns))["net"])["stat"])
    sims = np.array([x for x in sims if np.isfinite(x)])
    q = float(np.quantile(sims, S.NULL_QUANTILE))
    return {"stat": stat, "null_q95": q, "null_mean": float(sims.mean()), "draws": len(sims),
            "pass": bool(np.isfinite(stat) and stat > q)}
