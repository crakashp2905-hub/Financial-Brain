"""Portfolio stress tests: what a book would have lost in India's own bad episodes, and under fixed shocks.

Historical replays use each held name's own adjusted closes over the episode; a name that did not trade then is
stood in for by its beta times the Nifty 500's move (said in the output, never silent). The episodes are the ones the
price archive covers (2015 on); 2008 is outside it and is replaced by the worst of these, not invented.

    demonetisation      2016-11-08 .. 2016-12-26
    ilfs_liquidity      2018-08-28 .. 2018-10-26   (IL&FS default; NBFC and mid-cap liquidity crisis)
    covid_crash         2020-02-19 .. 2020-03-23
    rate_shock_2022     2022-01-17 .. 2022-06-17   (global rate rises, FPI outflow)
    smallcap_2024       2024-09-26 .. 2025-03-04

Fixed shocks: the market down 20% through each name's beta; the book's largest sector down a further 15%
(a rotation); liquidity stress - the sessions needed to sell everything at 10% of an ADV that has halved.
A currency (INR) shock is not modelled: the store holds no exchange-rate series or per-company FX exposure.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

EPISODES = {
    "demonetisation": ("2016-11-08", "2016-12-26"),
    "ilfs_liquidity": ("2018-08-28", "2018-10-26"),
    "covid_crash": ("2020-02-19", "2020-03-23"),
    "rate_shock_2022": ("2022-01-17", "2022-06-17"),
    "smallcap_2024": ("2024-09-26", "2025-03-04"),
}
MARKET_SHOCK, SECTOR_SHOCK = -0.20, -0.15
PARTICIPATION, ADV_HAIRCUT = 0.10, 0.5
INDEX = "Nifty 500"


def episode_return(prices: pd.Series, start: str, end: str) -> float | None:
    p = prices.dropna()
    if p.empty:
        return None
    a, b = p[p.index <= pd.Timestamp(start)], p[p.index <= pd.Timestamp(end)]
    if a.empty or b.empty or b.index[-1] <= pd.Timestamp(start):
        return None
    return float(b.iloc[-1] / a.iloc[-1] - 1)


def run(book: pd.DataFrame, prices: dict[str, pd.Series], index: pd.Series) -> dict:
    """``book``: ticker, value_inr, beta, sector, adv_inr. ``prices``: ticker -> adjusted close series.
    Returns per-scenario loss in rupees and as a share of the book, and which names were proxied."""
    total = float(book["value_inr"].sum())
    out = {"book_inr": total, "scenarios": {}}
    for name, (a, b) in EPISODES.items():
        m = episode_return(index, a, b)
        pnl, proxied = 0.0, []
        for r in book.itertuples():
            ret = episode_return(prices.get(r.ticker, pd.Series(dtype=float)), a, b)
            if ret is None:
                ret = (r.beta if np.isfinite(r.beta) else 1.0) * (m or 0.0)
                proxied.append(r.ticker)
            pnl += r.value_inr * ret
        out["scenarios"][name] = {"pnl_inr": pnl, "share": pnl / total if total else np.nan, "index": m,
                                  "proxied": proxied}
    beta = book["beta"].fillna(1.0)
    pnl = float((book["value_inr"] * beta * MARKET_SHOCK).sum())
    out["scenarios"]["market_down_20"] = {"pnl_inr": pnl, "share": pnl / total if total else np.nan}
    by_sector = book.groupby("sector")["value_inr"].sum()
    if len(by_sector):
        top = by_sector.idxmax()
        extra = float(by_sector.max() * SECTOR_SHOCK)
        out["scenarios"]["sector_rotation"] = {"sector": top, "pnl_inr": pnl + extra,
                                               "share": (pnl + extra) / total if total else np.nan}
    days = book["value_inr"] / (book["adv_inr"] * ADV_HAIRCUT * PARTICIPATION)
    out["liquidation_sessions"] = {"worst": float(days.max()) if len(days) else 0.0,
                                   "worst_name": str(book.loc[days.idxmax(), "ticker"]) if len(days) else None,
                                   "share_out_in_1_session": float(book.loc[days <= 1, "value_inr"].sum() / total) if total else np.nan}
    out["worst"] = min(((k, v["share"]) for k, v in out["scenarios"].items()), key=lambda kv: kv[1])
    out["not_modelled"] = ["INR shock (no exchange-rate series)", "2008 (before the price archive)"]
    return out


def book_from_holdings(rcon, holdings: pd.DataFrame, asof) -> pd.DataFrame:
    """``holdings``: tradingsymbol, quantity, last_price -> the stress ``book`` with beta and home sector measured
    the way the owner's book page measures them (graph/exposure, 250 sessions)."""
    from ..graph import exposure as X
    syms = list(holdings["tradingsymbol"])
    px = rcon.execute("""SELECT ticker, business_date, close_adj FROM adjusted_prices WHERE business_date <= ?
                         AND business_date > ?::DATE - INTERVAL 400 DAY AND close_adj > 0
                         AND ticker IN (SELECT UNNEST(?))""", [asof, asof, syms]).df()
    rets = px.pivot_table(index="business_date", columns="ticker", values="close_adj").pct_change(fill_method=None).tail(250)
    idx = rcon.execute("""SELECT index_name, business_date, close_level FROM index_levels_canonical WHERE variant = 'PRICE'
                          AND business_date <= ? AND business_date > ?::DATE - INTERVAL 400 DAY AND index_name IN (SELECT UNNEST(?))""",
                       [asof, asof, [X.MARKET, *X.SECTORS]]).df().pivot_table(index="business_date", columns="index_name",
                                                                             values="close_level")
    ex, _ = X.build_from_returns(rets, idx.pct_change(fill_method=None).reindex(rets.index))
    beta, sector = dict(zip(ex["ticker"], ex["beta_market"])), dict(zip(ex["ticker"], ex["home_sector"]))
    adv = dict(rcon.execute("""SELECT p.ticker, f.adv20 FROM features f JOIN adjusted_prices p USING (lineage, business_date)
                               WHERE f.business_date = ? AND p.ticker IN (SELECT UNNEST(?))""", [asof, syms]).fetchall())
    return pd.DataFrame({"ticker": syms, "value_inr": holdings["quantity"].astype(float) * holdings["last_price"].astype(float),
                         "beta": [beta.get(s, np.nan) for s in syms], "sector": [sector.get(s) or "unassigned" for s in syms],
                         "adv_inr": [adv.get(s, np.nan) for s in syms]})


def load_inputs(rcon, tickers: list[str]) -> tuple[dict, pd.Series]:
    d = rcon.execute("SELECT ticker, business_date, close_adj FROM adjusted_prices WHERE ticker IN (SELECT UNNEST(?)) "
                     "AND close_adj > 0", [tickers]).df()
    d["business_date"] = pd.to_datetime(d["business_date"])
    prices = {t: g.set_index("business_date")["close_adj"].sort_index() for t, g in d.groupby("ticker")}
    ix = rcon.execute("SELECT business_date, close_level FROM index_levels WHERE index_name = ? ORDER BY business_date",
                      [INDEX]).df()
    index = pd.Series(ix["close_level"].to_numpy(), index=pd.to_datetime(ix["business_date"]))
    return prices, index
