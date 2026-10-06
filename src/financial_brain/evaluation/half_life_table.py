"""Every signal at every horizon: not only its IC, but what a trader would earn and pay.

For each signal and horizon h (sessions), on rebalance dates spaced h apart (so each window is independent):

    ic          rank correlation of the signal with the forward h-session return (500 most liquid names)
    excess      the top quintile's mean forward return minus the universe mean
    hit_rate    the share of rebalances on which that excess was positive
    sharpe      annualised mean / sd of the per-rebalance excess
    mfe / mae   the median best and worst point (vs entry) of top-quintile names along the way
    turnover    the share of the top quintile replaced at each rebalance
    cost_drag   turnover x a round trip (both legs) - what holding the quintile at this horizon costs
    net         excess - cost_drag

The intraday rows (5 minutes to the close) come from ``intraday_half_life`` and carry IC only. Descriptive, not a
new claim: the pre-registered half-life result stands; this table is what a trader reads to choose a holding period.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

HORIZONS = (1, 3, 5, 10, 20, 40, 60)
ROUND_TRIP = 0.003            #: delivery round trip for the 500 most liquid names, fees + spread + impact (approx.)
QUINTILE = 0.2


def table(close: pd.DataFrame, signals: dict[str, pd.DataFrame], universe: pd.DataFrame, *,
          horizons=HORIZONS, round_trip: float = ROUND_TRIP, step0: int = 5) -> pd.DataFrame:
    """``close``: sessions x names. ``signals``: name -> sessions x names (higher = better). ``universe``: sessions x
    names, True where the name is eligible. All aligned on the same index and columns."""
    C = close.to_numpy(dtype=float)
    U = universe.reindex_like(close).fillna(False).to_numpy(dtype=bool)
    n = len(close)
    rows = []
    for name, sig in signals.items():
        S = sig.reindex_like(close).to_numpy(dtype=float)
        for h in horizons:
            step = max(h, step0)
            ics, exc, mfes, maes, turns = [], [], [], [], []
            prev_top = None
            for i in range(260, n - h, step):
                ok = U[i] & np.isfinite(S[i]) & np.isfinite(C[i]) & np.isfinite(C[i + h]) & (C[i] > 0)
                if ok.sum() < 100:
                    continue
                fwd = C[i + h, ok] / C[i, ok] - 1
                s = S[i, ok]
                ics.append(pd.Series(s).rank().corr(pd.Series(fwd).rank()))
                k = max(1, int(QUINTILE * ok.sum()))
                top = np.argsort(-s)[:k]
                exc.append(fwd[top].mean() - fwd.mean())
                path = C[i + 1:i + h + 1][:, ok][:, top] / C[i, ok][top] - 1
                with np.errstate(invalid="ignore"):
                    mfes.append(np.nanmedian(np.nanmax(path, axis=0)))
                    maes.append(np.nanmedian(np.nanmin(path, axis=0)))
                names = set(np.flatnonzero(ok)[top])
                if prev_top is not None:
                    turns.append(1 - len(names & prev_top) / len(names))
                prev_top = names
            if len(exc) < 10:
                continue
            e = np.array(exc)
            turnover = float(np.mean(turns)) if turns else 1.0
            drag = turnover * round_trip
            rows.append({"signal": name, "h": h, "rebalances": len(e), "ic": float(np.nanmean(ics)),
                         "excess": float(e.mean()), "hit_rate": float((e > 0).mean()),
                         "sharpe": float(e.mean() / e.std(ddof=1) * np.sqrt(252 / h)) if e.std(ddof=1) > 0 else float("nan"),
                         "mfe": float(np.nanmedian(mfes)), "mae": float(np.nanmedian(maes)), "turnover": turnover,
                         "cost_drag": drag, "net": float(e.mean() - drag)})
    return pd.DataFrame(rows)


def best_horizon(t: pd.DataFrame) -> pd.DataFrame:
    """Per signal, the horizon with the highest net per session held - what a trader should hold it for."""
    t = t.assign(net_per_session=t["net"] / t["h"])
    return t.loc[t.groupby("signal")["net_per_session"].idxmax(), ["signal", "h", "net", "net_per_session", "sharpe"]]


def load(con, *, start, end, names: int = 500):
    """Close matrix, signal matrices and the liquidity universe from the research database."""
    from ..opportunity import engine as E
    f = con.execute("""SELECT lineage, business_date, adv20, vol_60, mom_12_1, dist_52w_high, ret_20d FROM features
                       WHERE business_date BETWEEN ? AND ?""", [start, end]).df()
    px = con.execute("""SELECT lineage, business_date, close_adj FROM adjusted_prices
                        WHERE business_date BETWEEN ? AND ?::DATE + INTERVAL 120 DAY AND close_adj > 0""", [start, end]).df()
    close = px.pivot_table(index="business_date", columns="lineage", values="close_adj").sort_index()
    piv = {c: f.pivot_table(index="business_date", columns="lineage", values=c).reindex(index=close.index, columns=close.columns)
           for c in ("adv20", "vol_60", "mom_12_1", "dist_52w_high", "ret_20d")}
    universe = piv["adv20"].rank(axis=1, ascending=False) <= names
    comp = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    for d in close.index[::5]:
        row = pd.DataFrame({c: piv[c].loc[d] for c in ("vol_60", "mom_12_1", "dist_52w_high", "ret_20d")}).dropna()
        if len(row) > 100:
            comp.loc[d, row.index] = E.composite(row).to_numpy()
    signals = {"momentum": piv["mom_12_1"], "52w_high": piv["dist_52w_high"], "low_vol": -piv["vol_60"],
               "reversal": -piv["ret_20d"], "composite": comp}
    return close, signals, universe
