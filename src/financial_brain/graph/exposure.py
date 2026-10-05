"""Who is exposed to what, measured from returns, and what a shock to one node implies for the rest.

There is no supplier/customer/lender dataset in this archive, so exposure is *measured*, not asserted. For every
liquid name, over the trailing year of daily returns:

    market       beta to the Nifty 500
    home sector  the sector index whose market-removed return best explains the name's market-removed return, and
                 its beta to it (Auto, Bank, Energy, FMCG, IT, Media, Metal, Pharma, PSU Bank, Realty,
                 Infrastructure, Commodities, Consumption)
    rates        beta of the market-removed return to the 10-year G-Sec price index
    volatility   beta to the daily change in India VIX
    peers        the names whose market-removed returns move most with it

``propagate`` answers "if crude takes Energy down 3% and the market 1%, who moves and by how much?" by passing the
shock through those betas; ``propagate_stock`` answers "if this name moves 8% on its own news, who usually moves
with it?" through the residual co-movement.

**What this is not:** a forecast. The pre-registered spillover test found that a company's results reaction does
not carry into its promoter siblings afterwards; these betas describe *simultaneous* co-movement, which is what a
risk view and a what-if need. Whether a shock propagates with a lag is a separate claim and is untested here.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MARKET = "Nifty 500"
SECTORS = ("Nifty Auto", "Nifty Bank", "Nifty Energy", "Nifty FMCG", "Nifty IT", "Nifty Media", "Nifty Metal",
           "Nifty Pharma", "Nifty PSU Bank", "Nifty Realty", "Nifty Infrastructure", "Nifty Commodities",
           "Nifty India Consumption")
RATES = "Nifty 10 yr Benchmark G-Sec"
VIX = "India VIX"
LOOKBACK = 250


class ExposureError(ValueError):
    pass


def _beta(y: np.ndarray, x: np.ndarray) -> float:
    ok = np.isfinite(y) & np.isfinite(x)
    if ok.sum() < 60 or np.var(x[ok]) == 0:
        return float("nan")
    return float(np.cov(y[ok], x[ok])[0, 1] / np.var(x[ok], ddof=1))


def build_from_returns(stocks: pd.DataFrame, factors: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``stocks``: daily returns, columns = tickers. ``factors``: daily returns of the market, sectors, rates and
    VIX changes, columns = their names. Returns (exposures, market-removed stock residuals)."""
    if MARKET not in factors:
        raise ExposureError("the market factor is required")
    m = factors[MARKET].to_numpy()
    sec_res = {}
    for s in SECTORS:
        if s in factors:
            b = _beta(factors[s].to_numpy(), m)
            sec_res[s] = factors[s].to_numpy() - b * m
    rows, resid = [], {}
    for t in stocks.columns:
        y = stocks[t].to_numpy()
        bm = _beta(y, m)
        if not np.isfinite(bm):
            continue
        e = y - bm * m
        resid[t] = e
        best, best_c, best_b = None, 0.0, float("nan")
        for s, x in sec_res.items():
            ok = np.isfinite(e) & np.isfinite(x)
            if ok.sum() < 60:
                continue
            c = np.corrcoef(e[ok], x[ok])[0, 1]
            if c > best_c:                              # the strongest POSITIVE link: a home, not an inverse hedge
                best, best_c, best_b = s, c, _beta(e, x)
        rows.append({"ticker": t, "beta_market": bm, "home_sector": best, "sector_corr": float(best_c),
                     "beta_sector": best_b,
                     "beta_rates": _beta(e, factors[RATES].to_numpy()) if RATES in factors else float("nan"),
                     "beta_vix": _beta(e, factors[VIX].to_numpy()) if VIX in factors else float("nan"),
                     "resid_vol": float(np.nanstd(e, ddof=1) * np.sqrt(252))})
    return pd.DataFrame(rows), pd.DataFrame(resid, index=stocks.index)


def build(con, as_of, *, names: int = 400, lookback: int = LOOKBACK):
    tick = [r[0] for r in con.execute("""
        SELECT p.ticker FROM features f JOIN adjusted_prices p ON p.lineage = f.lineage AND p.business_date = f.business_date
        WHERE f.business_date = ? AND f.adv20 >= 1e7 ORDER BY f.adv20 DESC LIMIT ?""", [as_of, names]).fetchall()]
    px = con.execute("""SELECT ticker, business_date, close_adj FROM adjusted_prices
                        WHERE business_date <= ? AND business_date > ?::DATE - INTERVAL 400 DAY AND close_adj > 0
                          AND ticker IN (SELECT UNNEST(?))""", [as_of, as_of, tick]).df()
    stocks = px.pivot_table(index="business_date", columns="ticker", values="close_adj").pct_change().tail(lookback)
    idx = con.execute("""SELECT index_name, business_date, close_level FROM index_levels_canonical
                         WHERE variant = 'PRICE' AND business_date <= ? AND business_date > ?::DATE - INTERVAL 400 DAY
                           AND index_name IN (SELECT UNNEST(?))""", [as_of, as_of, [MARKET, *SECTORS, RATES, VIX]]).df()
    lv = idx.pivot_table(index="business_date", columns="index_name", values="close_level")
    f = lv.pct_change()
    if VIX in lv:
        f[VIX] = lv[VIX].diff()                         # VIX in points: its change, not its return
    f = f.reindex(stocks.index)
    return build_from_returns(stocks, f)


def propagate(exposures: pd.DataFrame, shocks: dict[str, float]) -> pd.DataFrame:
    """Implied simultaneous move per name for shocks to the market, sector indices, rates (index return) and VIX
    (points). Sector shocks are taken as moves *beyond* what the market shock implies for that sector."""
    m = shocks.get(MARKET, 0.0)
    out = exposures.copy()
    out["implied"] = out["beta_market"] * m
    for s, v in shocks.items():
        if s in SECTORS:
            hit = out["home_sector"] == s
            out.loc[hit, "implied"] += out.loc[hit, "beta_sector"].fillna(0.0) * v
    if RATES in shocks:
        out["implied"] += out["beta_rates"].fillna(0.0) * shocks[RATES]
    if VIX in shocks:
        out["implied"] += out["beta_vix"].fillna(0.0) * shocks[VIX]
    return out.sort_values("implied")


def propagate_stock(resid: pd.DataFrame, ticker: str, move: float, *, k: int = 10) -> pd.DataFrame:
    """Names whose market-removed returns move with ``ticker``'s, and the move each would show alongside a
    stock-specific ``move`` in it (residual beta x move)."""
    if ticker not in resid:
        raise ExposureError(f"{ticker} has no residual series")
    y = resid[ticker].to_numpy()
    rows = []
    for t in resid.columns:
        if t == ticker:
            continue
        x = resid[t].to_numpy()
        ok = np.isfinite(x) & np.isfinite(y)
        if ok.sum() < 60:
            continue
        c = float(np.corrcoef(x[ok], y[ok])[0, 1])
        b = float(np.cov(x[ok], y[ok])[0, 1] / np.var(y[ok], ddof=1))
        rows.append({"ticker": t, "residual_corr": c, "implied": b * move})
    return pd.DataFrame(rows).sort_values("residual_corr", ascending=False).head(k).reset_index(drop=True)
