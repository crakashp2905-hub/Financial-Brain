"""Trial 7, ``PRE-REGISTERED chart and candle setups in futures``: 17 chart and candle setups, each with the side a
chart trader states, traded as stock futures hedged with the equal-weighted F&O universe.

Setup completes at the close of t; enter at the close of t+1; exit at the close of t+6. One event per name and setup
per five sessions. Outcome = side x (stock - equal-weighted F&O) - 20 bp. Period series: mean by entry month.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

HOLD, LAG, COST = 5, 1, 0.0020

#: setup -> (side, how it fires from the library frame ``f`` and the bars ``b``)
SETUPS = {
    "bull_marubozu": (+1, lambda f, b: f["c_marubozu"].fillna(0) > 0),
    "hammer": (+1, lambda f, b: f["c_hammer"].fillna(0) > 0),
    "bull_engulf": (+1, lambda f, b: f["c_bull_engulf"].fillna(0) > 0),
    "morning_star": (+1, lambda f, b: f["c_morning_star"].fillna(0) > 0),
    "sweep_lows": (+1, lambda f, b: f["sweep_bull"].fillna(0) > 0),
    "bos_up": (+1, lambda f, b: f["bos_up"].fillna(0) > 0),
    "fvg_bull_retest": (+1, lambda f, b: f["fvg_bull_retest"].fillna(0) > 0),
    "gap_up_unfilled": (+1, lambda f, b: f["gap_up_unfilled"].fillna(0) > 0),
    "high55_volume": (+1, lambda f, b: (b["c"] >= b["c"].rolling(55, min_periods=55).max())
                      & (b["v"] >= 1.5 * b["v"].rolling(20, min_periods=20).mean().shift(1))),
    "bear_engulf": (-1, lambda f, b: f["c_bear_engulf"].fillna(0) > 0),
    "shooting_star": (-1, lambda f, b: f["c_shooting_star"].fillna(0) > 0),
    "evening_star": (-1, lambda f, b: f["c_evening_star"].fillna(0) > 0),
    "sweep_highs": (-1, lambda f, b: f["sweep_bear"].fillna(0) > 0),
    "bos_down": (-1, lambda f, b: f["bos_down"].fillna(0) > 0),
    "fvg_bear_retest": (-1, lambda f, b: f["fvg_bear_retest"].fillna(0) > 0),
    "gap_down_unfilled": (-1, lambda f, b: f["gap_down_unfilled"].fillna(0) > 0),
    "low55_volume": (-1, lambda f, b: (b["c"] <= b["c"].rolling(55, min_periods=55).min())
                     & (b["v"] >= 1.5 * b["v"].rolling(20, min_periods=20).mean().shift(1))),
}


def signals(rcon, symbols_to_lineage: dict[str, str]) -> dict[str, pd.DataFrame]:
    """symbol -> sessions x setups (bool), from the library on adjusted daily bars."""
    from ..features import library as LB
    from ..features import technical as TE
    rcon.execute(TE.BARS_SQL)
    bars = rcon.execute("""SELECT lineage, d, o, h, l, c, v FROM _tbars WHERE lineage IN (SELECT UNNEST(?))
                           ORDER BY lineage, d""", [list(symbols_to_lineage.values())]).df()
    bars["d"] = pd.to_datetime(bars["d"])
    by = {lin: g.reset_index(drop=True) for lin, g in bars.groupby("lineage")}
    out = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for sym, lin in symbols_to_lineage.items():
            b = by.get(lin)
            if b is None or len(b) < 120:
                continue
            f = LB.compute(b[["d", "o", "h", "l", "c", "v"]])
            out[sym] = pd.DataFrame({k: fire(f, b).to_numpy() for k, (_, fire) in SETUPS.items()}, index=b["d"])
    return out


def forward(C: pd.DataFrame, M: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Per signal session t: each name's return from the close of t+1 to the close of t+1+HOLD, and the
    equal-weighted F&O return over the same window (names live at t)."""
    R = C.shift(-(LAG + HOLD)) / C.shift(-LAG) - 1
    EW = R.where(M).mean(axis=1)
    return R, EW


def events(sig: dict[str, pd.DataFrame], C: pd.DataFrame, M: pd.DataFrame) -> pd.DataFrame:
    R, EW = forward(C, M)
    pos = {d: i for i, d in enumerate(C.index)}
    rows = []
    for sym, S in sig.items():
        if sym not in C.columns:
            continue
        S = S.reindex(C.index).fillna(False).astype(bool)
        for setup, (side, _) in SETUPS.items():
            last = -10**9
            for d in S.index[S[setup].to_numpy()]:
                i = pos[d]
                if i - last < HOLD:
                    continue                                    # one event per name and setup per five sessions
                if not M.at[d, sym]:
                    continue
                r = R.at[d, sym]
                if not np.isfinite(r) or not np.isfinite(EW.at[d]):
                    continue
                last = i
                rows.append({"setup": setup, "side": side, "d": d, "symbol": sym,
                             "gross": side * (r - EW.at[d]), "out": side * (r - EW.at[d]) - COST})
    return pd.DataFrame(rows)


def pool_for(C: pd.DataFrame, M: pd.DataFrame, side: int):
    R, EW = forward(C, M)
    X = side * R.sub(EW, axis=0) - COST

    def pool(d, n, rng):
        row = X.loc[d][M.loc[d] & X.loc[d].notna()]
        return rng.choice(row.to_numpy(), size=min(n, len(row)), replace=False) if len(row) else []
    return pool
