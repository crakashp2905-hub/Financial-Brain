"""Group 3 continued: cash-and-carry (#48), its reverse (#49), basis trading (#55) and index arbitrage (#45).

Kite serves a futures contract's history *continuously* (expired months stitched), which gives each F&O name's near-month
future close every day. Against the unadjusted spot close (futures are not adjusted for corporate actions, so the raw
spot is the like-for-like price) that gives the basis, and from the calendar the contract each close belongs to.

    cash-and-carry   buy spot, sell the near future on the first session of a contract, hold to expiry, when the
                     basis must close. Earned: the starting basis minus costs - a riskless rate of return, so it is
                     judged against the risk-free rate, not zero.
    reverse          when the future trades below spot by more than costs: sell spot (in India only through securities
                     lending, so theoretical), buy the future
    basis trading    cash-and-carry only when the annualised basis is above a threshold
    index arbitrage  the same on the Nifty 50 future against the index level (the basket itself is not tradeable at
                     one price, so this measures the carry the future offers, not a capturable mispricing)

Expiry: the last Thursday of the month until August 2025, the last Tuesday from September 2025 (NSE's change), moved to
the previous session when it is a holiday.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

RISK_FREE = 0.065
#: Costs of the round trip as a fraction of notional: spot buy and sell (STT 0.1% each leg, stamp 0.015% on the buy,
#: exchange and GST about 0.004% a leg, DP charge on the sale, brokerage on 1 lakh), futures sale (STT 0.02%) and its
#: exit at expiry (cash settlement, no further STT), exchange charges on the future.
COSTS = 0.001 + 0.001 + 0.00015 + 0.0001 + 0.00015 + 0.0002 + 0.00005


def expiry_for(d: date, sessions: list[date]) -> date:
    """The monthly expiry the near contract trading on ``d`` belongs to."""
    def last_weekday(y, m, wd):
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        x = nxt - timedelta(days=1)
        while x.weekday() != wd:
            x -= timedelta(days=1)
        return x

    def adjusted(x):
        prior = [s for s in sessions if s <= x]
        return prior[-1] if prior else x

    y, m = d.year, d.month
    for _ in range(2):
        wd = 3 if date(y, m, 1) < date(2025, 9, 1) else 1
        e = adjusted(last_weekday(y, m, wd))
        if d <= e:
            return e
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return e


def contracts(fut: pd.Series, spot: pd.Series, sessions: list[date]) -> pd.DataFrame:
    """One row per monthly contract: the first session's basis, the expiry's basis, the days held."""
    df = pd.DataFrame({"fut": fut, "spot": spot}).dropna()
    df = df[(df["fut"] > 0) & (df["spot"] > 0)]
    if df.empty:
        return pd.DataFrame()
    df["expiry"] = [expiry_for(d, sessions) for d in df.index]
    rows = []
    for e, g in df.groupby("expiry"):
        g = g.sort_index()
        if len(g) < 10 or g.index[-1] != e:
            continue                                  # need the contract from its start to its expiry session
        first, last = g.iloc[0], g.iloc[-1]
        basis0 = first["fut"] / first["spot"] - 1
        days = (e - g.index[0]).days
        if abs(basis0) > 0.05 or days <= 0:
            continue                                  # a corporate action between spot and future, not a basis
        # long spot, short future, both closed at expiry: (S_T - S_0) - (F_T - F_0), per unit of starting spot
        pnl = ((last["spot"] - first["spot"]) - (last["fut"] - first["fut"])) / first["spot"]
        rows.append({"start": g.index[0], "expiry": e, "days": days, "basis0": basis0,
                     "basis0_pa": basis0 * 365 / days, "basis_end": last["fut"] / last["spot"] - 1,
                     "gross": pnl, "net": pnl - COSTS, "rf": RISK_FREE * days / 365})
    return pd.DataFrame(rows)


def summarise(c: pd.DataFrame) -> dict:
    if c.empty:
        return {}
    ex = c["net"] - c["rf"]
    return {"contracts": int(len(c)), "basis_pa_median": float(c["basis0_pa"].median()),
            "gross_pa": float((c["gross"] * 365 / c["days"]).mean()), "net_pa": float((c["net"] * 365 / c["days"]).mean()),
            "excess_over_rf_per_contract": float(ex.mean()),
            "t_excess": float(ex.mean() / (ex.std(ddof=1) / np.sqrt(len(ex)))) if len(ex) > 2 and ex.std(ddof=1) > 0 else float("nan"),
            "share_beating_rf": float((ex > 0).mean()), "worst_net": float(c["net"].min()),
            "basis_at_expiry_abs_median_bp": float(c["basis_end"].abs().median() * 1e4)}
