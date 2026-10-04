"""Classic swing setups, tested after costs and with a stop.

Pre-registered in ``PRE-REGISTERED swing and intraday setups with stops``. A setup is a rule on the features
at the close of day t. The trade is entered at the **close of t+1** (a one-day lag, so the signal is never
traded on the price that produced it) and exited at the close of ``t+1+h``. The outcome is market-adjusted:
minus the median return of every liquid name over the same window. Costs are the sized round trip at a
Rs 5 lakh order - fixed statutory charges plus square-root impact against the name's turnover.

The optional stop watches **daily closes** after entry and exits at the first close below
``entry x (1 - k x ATR% x sqrt(h))``, holding cash at the cash rate afterwards. A close-based stop
understates gap risk, which is stated wherever its result is quoted.

One observation per signal date: results season and sell-offs stack hundreds of signals on the same day, so
the standard error is clustered by date.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..costs.india import CostModel, Segment

SETUPS = {
    "SW1 pullback in uptrend": lambda f: (f["above_ma200"] == 1) & (f["mom_12_1"] > 0) & (f["ret_5d"] <= -0.05),
    "SW2 oversold in uptrend": lambda f: (f["above_ma200"] == 1) & (f["rsi_14"] < 30),
    "SW3 breakout": lambda f: (f["dist_52w_high"] >= -0.01) & (f["adx_14"] > 25) & (f["above_ma50"] == 1),
    "SW4 strength continuation": lambda f: (f["above_ma50"] == 1) & (f["above_ma200"] == 1)
    & (f["bb_pct_20"] > 0.8) & (f["adx_14"] > 25),
}
COLUMNS = ("above_ma200", "above_ma50", "mom_12_1", "ret_5d", "rsi_14", "dist_52w_high", "adx_14",
           "bb_pct_20", "atr_14_pct", "vol_60", "adv20")
HORIZONS = (5, 10)
STOP_K = 2.0
CASH_RATE = 0.06
ORDER_INR = 500_000.0
PARTICIPATION = 0.10
MIN_ADV = 1e8                     #: Rs 10 crore a day


class SwingError(ValueError):
    pass


def fee_fraction() -> float:
    """Statutory + broker round trip on a delivery order, as a fraction of the amount (size-independent)."""
    rt = CostModel().round_trip(turnover=ORDER_INR, segment=Segment.DELIVERY, include_impact=False)
    return rt["total"] / ORDER_INR


def sized_cost(vol_annual: np.ndarray, adv: np.ndarray) -> np.ndarray:
    order = np.minimum(ORDER_INR, PARTICIPATION * adv)
    impact = 2.0 * (vol_annual / np.sqrt(252.0)) * np.sqrt(order / adv)
    return fee_fraction() + impact


def load(con, start, end) -> dict:
    cols = ", ".join(COLUMNS)
    f = con.execute(f"""SELECT lineage, business_date, {cols} FROM features
                        WHERE business_date BETWEEN ? AND ? AND adv20 >= ?""", [start, end, MIN_ADV]).df()
    px = con.execute("""SELECT lineage, business_date, close_adj FROM adjusted_prices
                        WHERE close_adj > 0 AND business_date >= ?""", [start]).df()
    wide = px.pivot_table(index="business_date", columns="lineage", values="close_adj")
    out = {"P": wide}
    for c in COLUMNS:
        # Boolean features (above_ma200) arrive as nullable booleans; as floats a missing value is NaN and a
        # comparison against it is simply False, instead of an ambiguous NA that crashes the mask.
        out[c] = f.pivot_table(index="business_date", columns="lineage", values=c, dropna=False).reindex(
            index=wide.index, columns=wide.columns).astype("float64")
    return out


def date_clustered(values: np.ndarray, dates: np.ndarray) -> dict:
    d = pd.DataFrame({"v": values, "d": dates}).dropna()
    g = d.groupby("d")["v"].mean()
    if len(g) < 30:
        return {"mean": float(d["v"].mean()) if len(d) else float("nan"), "t": float("nan"),
                "trades": int(len(d)), "dates": int(len(g))}
    return {"mean": float(d["v"].mean()), "t": float(g.mean() / (g.std(ddof=1) / np.sqrt(len(g)))),
            "trades": int(len(d)), "dates": int(len(g))}


def run(data: dict, *, horizons=HORIZONS, stop_k: float = STOP_K, cash_rate: float = CASH_RATE) -> dict:
    P = data["P"].to_numpy()
    T, N = P.shape
    liquid = np.isfinite(data["adv20"].to_numpy())
    cash_day = (1 + cash_rate) ** (1 / 252) - 1
    feats = {c: data[c] for c in COLUMNS}
    res = {}
    for h in horizons:
        entry = np.full_like(P, np.nan)
        exitp = np.full_like(P, np.nan)
        entry[:T - 1 - h] = P[1:T - h]
        exitp[:T - 1 - h] = P[1 + h:T]
        fwd = exitp / entry - 1.0
        med = np.nanmedian(np.where(liquid, fwd, np.nan), axis=1)
        # The MEAN of the liquid universe over the same window. The pre-registered outcome subtracts the
        # median, but returns are right-skewed, so a random liquid name earns the mean, not the median,
        # and the gap between them (about +0.4% over 10 sessions) is a skew premium any long book collects.
        # The edge of a setup is what it earns beyond a random name, so both are reported.
        mean_u = np.nanmean(np.where(liquid, fwd, np.nan), axis=1)
        cost = sized_cost(data["vol_60"].to_numpy(), data["adv20"].to_numpy())
        for name, rule in SETUPS.items():
            mask = rule(feats).to_numpy() & liquid & np.isfinite(fwd)
            ti, ni = np.nonzero(mask)
            if len(ti) == 0:
                continue
            gross = fwd[ti, ni] - med[ti]
            net = gross - cost[ti, ni]
            # stop variant on the same trades
            paths = np.stack([P[ti + 1 + k, ni] for k in range(h + 1)], axis=1)           # trades x (h+1)
            rel = paths / paths[:, [0]] - 1.0
            stop_lvl = -stop_k * data["atr_14_pct"].to_numpy()[ti, ni] * np.sqrt(h)
            hit = rel[:, 1:] <= stop_lvl[:, None]
            fired = hit.any(axis=1)
            first = np.where(fired, hit.argmax(axis=1), h - 1)
            exit_ret = rel[np.arange(len(ti)), first + 1]
            ret_stop = np.where(fired, (1 + exit_ret) * (1 + cash_day) ** (h - 1 - first) - 1.0, rel[:, -1])
            net_stop = ret_stop - med[ti] - cost[ti, ni]
            gross_m = fwd[ti, ni] - mean_u[ti]                       # edge over a random liquid name
            net_m = gross_m - cost[ti, ni]
            net_stop_m = ret_stop - mean_u[ti] - cost[ti, ni]
            dates = data["P"].index[ti].to_numpy()
            res[(name, h)] = {"gross": date_clustered(gross, dates), "net": date_clustered(net, dates),
                              "net_stop": date_clustered(net_stop, dates),
                              "gross_vs_mean": date_clustered(gross_m, dates),
                              "net_vs_mean": date_clustered(net_m, dates),
                              "net_stop_vs_mean": date_clustered(net_stop_m, dates),
                              "skew_gap": float(np.nanmean(mean_u[ti] - med[ti])),
                              "stopped": float(fired.mean()), "cost": float(np.nanmean(cost[ti, ni])),
                              "win_rate_net": float((net > 0).mean())}
    return res
