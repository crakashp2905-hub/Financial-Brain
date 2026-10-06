"""The owner's trading journal: round trips from the Kite trade book, each with an autopsy, and - once there are
enough - a report on what the owner is good and bad at.

Kite's trade book holds only the current day, so the journal grows from the evening refresh onwards. A round trip
is matched first-in-first-out per symbol and product. Each closed trip is attributed:

    market      beta x the Nifty 500's move over the holding days
    sector      the home sector's move beyond the market, x the name's sector beta
    selection   what the stock did beyond both
    entry       the fill against that day's close (positive: bought cheaper than the close)
    exit        the fill against that day's close (positive: sold richer than the close)
    costs       the cost schedule's charges for the round trip

They add up to the trip's net return. The report waits for ``MIN_TRIPS`` before it judges anything: twenty trades
say almost nothing about a trader.
"""
from __future__ import annotations

from collections import deque

import numpy as np
import pandas as pd

MIN_TRIPS = 30


def round_trips(trades: pd.DataFrame) -> pd.DataFrame:
    """FIFO match of buys and sells per (symbol, product). Opening sells (intraday shorts) are matched too."""
    out = []
    t = trades.sort_values(["ts", "trade_id"])
    for (sym, prod), g in t.groupby(["tradingsymbol", "product"]):
        lots: deque = deque()                       # (sign, qty, price, ts, day)
        for r in g.itertuples():
            sign = 1 if r.side == "buy" else -1
            qty = float(r.quantity)
            while qty > 0 and lots and lots[0][0] != sign:
                osign, oq, opx, ots, oday = lots[0]
                m = min(qty, oq)
                entry, exit_ = opx, float(r.price)
                gross = (exit_ - entry) * m * osign
                out.append({"symbol": sym, "product": prod, "side": "long" if osign > 0 else "short", "qty": m,
                            "entry_ts": ots, "entry_day": oday, "entry_px": entry, "exit_ts": r.ts, "exit_day": r.day,
                            "exit_px": exit_, "gross": gross, "ret": gross / (entry * m)})
                qty -= m
                if m == oq:
                    lots.popleft()
                else:
                    lots[0] = (osign, oq - m, opx, ots, oday)
            if qty > 0:
                lots.append((sign, qty, float(r.price), r.ts, r.day))
    return pd.DataFrame(out)


def autopsy(trip: dict, *, stock_ret: float, market_ret: float, sector_ret: float | None, beta: float,
            sector_beta: float, entry_close: float, exit_close: float, cost: float) -> dict:
    """Close-to-close returns over the trip's days; the fills against those closes are entry and exit timing."""
    sign = 1 if trip["side"] == "long" else -1
    market = sign * beta * market_ret
    sector = sign * sector_beta * (sector_ret - market_ret) if sector_ret is not None else 0.0
    selection = sign * stock_ret - market - sector
    entry = sign * (entry_close / trip["entry_px"] - 1)
    exit_ = sign * (trip["exit_px"] / exit_close - 1)
    net = trip["ret"] - cost
    return {"market": market, "sector": sector, "selection": selection, "entry": entry, "exit": exit_,
            "costs": -cost, "net": net, "residual": net - (market + sector + selection + entry + exit_ - cost)}


def report(trips: pd.DataFrame) -> dict:
    """What the owner is good and bad at, once there is enough to say it."""
    n = len(trips)
    out = {"trips": n, "judged": n >= MIN_TRIPS}
    if n == 0:
        return out
    out["win_rate"] = float((trips["net"] > 0).mean())
    out["mean_net"] = float(trips["net"].mean())
    for k in ("selection", "entry", "exit", "market", "sector", "costs"):
        if k in trips:
            out[f"mean_{k}_bp"] = float(trips[k].mean() * 1e4)
    held = (pd.to_datetime(trips["exit_day"]) - pd.to_datetime(trips["entry_day"])).dt.days
    trips = trips.assign(held_bucket=pd.cut(held, [-1, 0, 5, 20, 10_000], labels=["intraday", "1-5 days", "6-20 days", "> 20 days"]))
    out["by_holding"] = trips.groupby("held_bucket", observed=True)["net"].agg(["count", "mean"]).to_dict("index")
    if n >= MIN_TRIPS:
        parts = {k: out.get(f"mean_{k}_bp", 0.0) for k in ("selection", "entry", "exit")}
        out["strength"] = max(parts, key=parts.get)
        out["weakness"] = min(parts, key=parts.get)
    return out


def attribute(rcon, trips: pd.DataFrame) -> pd.DataFrame:
    """Each closed trip's autopsy from the archive: closes on its entry and exit days, beta and home sector from the
    year before entry, the Nifty 500 and sector moves over the same days, and the cost schedule's charges."""
    from ..costs.india import CostModel, Segment
    from ..graph import exposure as X
    if trips.empty:
        return trips
    cm = CostModel()
    rows = []
    for t in trips.to_dict("records"):
        d0, d1 = pd.Timestamp(t["entry_day"]).date(), pd.Timestamp(t["exit_day"]).date()
        px = rcon.execute("""SELECT business_date, close_adj FROM adjusted_prices WHERE ticker = ? AND close_adj > 0
                             AND business_date > ?::DATE - INTERVAL 400 DAY AND business_date <= ? ORDER BY 1""",
                          [t["symbol"], d0, d1]).df()
        if px.empty:
            rows.append({**t, "attributed": False})
            continue
        px["business_date"] = pd.to_datetime(px["business_date"]).dt.date
        c = px.set_index("business_date")["close_adj"]
        if d0 not in c.index or d1 not in c.index:
            rows.append({**t, "attributed": False})
            continue
        idx = rcon.execute("""SELECT index_name, business_date, close_level FROM index_levels_canonical WHERE variant = 'PRICE'
                              AND business_date > ?::DATE - INTERVAL 400 DAY AND business_date <= ? AND index_name IN (SELECT UNNEST(?))""",
                           [d0, d1, [X.MARKET, *X.SECTORS]]).df()
        idx["business_date"] = pd.to_datetime(idx["business_date"]).dt.date
        lv = idx.pivot_table(index="business_date", columns="index_name", values="close_level")
        hist = c[c.index <= d0].pct_change().tail(250).to_frame(t["symbol"])
        ex, _ = X.build_from_returns(hist, lv[lv.index <= d0].pct_change().reindex(hist.index))
        beta = float(ex["beta_market"].iloc[0]) if len(ex) else 1.0
        sec = ex["home_sector"].iloc[0] if len(ex) else None
        sbeta = float(ex["beta_sector"].iloc[0]) if len(ex) and pd.notna(ex["beta_sector"].iloc[0]) else 0.0
        mret = float(lv.loc[d1, X.MARKET] / lv.loc[d0, X.MARKET] - 1) if X.MARKET in lv else 0.0
        sret = float(lv.loc[d1, sec] / lv.loc[d0, sec] - 1) if sec in lv else None
        seg = Segment.INTRADAY if t["product"] == "MIS" or d0 == d1 else Segment.DELIVERY
        cost = cm.round_trip(turnover=t["entry_px"] * t["qty"], segment=seg, include_impact=False)["total"] / (t["entry_px"] * t["qty"])
        a = autopsy(t, stock_ret=float(c[d1] / c[d0] - 1), market_ret=mret, sector_ret=sret, beta=beta, sector_beta=sbeta,
                    entry_close=float(c[d0]), exit_close=float(c[d1]), cost=cost)
        rows.append({**t, **a, "sector_name": sec, "attributed": True})
    return pd.DataFrame(rows)
