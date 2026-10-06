"""The owner's own book: holdings and positions read from Kite (read only), and what the brain says about each.

For every holding, by rules (never by a model's opinion):

    since cost      price against the owner's average cost, and against the market and its home sector
    thesis health   0-100 from checks the brain trusts: composite rank, trend, relative strength, the stop the
                    plan would set from the average cost, volatility expanding
    rule state      EXIT BY RULE only on the plan's tested exits (a close below the stop from your cost, or the
                    composite in the bottom 20%); otherwise HOLD (score >= 60) or REVIEW (below 60) - those bands are
                    descriptive and untested

and for the book as a whole: value, P&L, volatility, beta, the share at risk to the stops, the largest sector, and
which of today's opportunities would add the least risk for their alpha given what is already held.

Stored in ``data/my_portfolio.duckdb`` (gitignored). Information about the owner's own positions, not advice.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

PATH = Path("data") / "my_portfolio.duckdb"
HOLD, REVIEW, EXIT = "HOLD", "REVIEW", "EXIT BY RULE"

SCHEMA = """
CREATE TABLE IF NOT EXISTS holdings_snap (
    day DATE, tradingsymbol VARCHAR, exchange VARCHAR, isin VARCHAR, quantity DOUBLE, t1_quantity DOUBLE,
    average_price DOUBLE, last_price DOUBLE, close_price DOUBLE, pnl DOUBLE, day_change_percentage DOUBLE,
    PRIMARY KEY (day, tradingsymbol));
CREATE TABLE IF NOT EXISTS positions_snap (
    day DATE, tradingsymbol VARCHAR, exchange VARCHAR, product VARCHAR, quantity DOUBLE, average_price DOUBLE,
    last_price DOUBLE, pnl DOUBLE, PRIMARY KEY (day, tradingsymbol, product));
CREATE TABLE IF NOT EXISTS holding_entry (
    symbol VARCHAR PRIMARY KEY, first_day DATE, composite_pct DOUBLE, price DOUBLE);
CREATE TABLE IF NOT EXISTS my_trades (
    trade_id VARCHAR PRIMARY KEY, day DATE, ts VARCHAR, tradingsymbol VARCHAR, exchange VARCHAR, product VARCHAR,
    side VARCHAR, quantity DOUBLE, price DOUBLE);
"""


class BookError(ValueError):
    pass


def connect(path: Path = PATH, read_only: bool = False):
    if read_only:
        return duckdb.connect(str(path), read_only=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(SCHEMA)
    return con


def sync(con, day: date, *, holdings_fn, positions_fn, trades_fn) -> dict:
    """Store today's holdings, positions and trade book. Idempotent per day."""
    h = holdings_fn() or []
    con.execute("DELETE FROM holdings_snap WHERE day = ?", [day])
    for x in h:
        con.execute("INSERT INTO holdings_snap VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [day, x.get("tradingsymbol"), x.get("exchange"), x.get("isin"), x.get("quantity"),
                     x.get("t1_quantity"), x.get("average_price"), x.get("last_price"), x.get("close_price"),
                     x.get("pnl"), x.get("day_change_percentage")])
    p = (positions_fn() or {}).get("net", [])
    con.execute("DELETE FROM positions_snap WHERE day = ?", [day])
    for x in p:
        con.execute("INSERT INTO positions_snap VALUES (?,?,?,?,?,?,?,?)",
                    [day, x.get("tradingsymbol"), x.get("exchange"), x.get("product"), x.get("quantity"),
                     x.get("average_price"), x.get("last_price"), x.get("pnl")])
    t = trades_fn() or []
    for x in t:
        ts = str(x.get("fill_timestamp") or x.get("exchange_timestamp") or "")
        con.execute("INSERT OR IGNORE INTO my_trades VALUES (?,?,?,?,?,?,?,?,?)",
                    [str(x.get("trade_id")), day, ts, x.get("tradingsymbol"), x.get("exchange"), x.get("product"),
                     str(x.get("transaction_type", "")).lower(), x.get("quantity"), x.get("average_price")])
    return {"holdings": len(h), "positions": len(p), "trades": len(t)}


def latest(con) -> pd.DataFrame:
    df = con.execute("""SELECT * FROM holdings_snap WHERE day = (SELECT MAX(day) FROM holdings_snap)
                        AND quantity + COALESCE(t1_quantity, 0) > 0""").df()
    if df.empty:
        return df
    df["qty"] = df["quantity"] + df["t1_quantity"].fillna(0)
    df["value"] = df["qty"] * df["last_price"]
    df["cost"] = df["qty"] * df["average_price"]
    df["since_cost"] = df["last_price"] / df["average_price"] - 1
    return df


def thesis_health(f: pd.Series, *, avg_price: float, last_price: float, stop_pct: float,
                  composite_pct: float | None) -> dict:
    """``f``: the name's features today. Returns a 0-100 score, the checks behind it and the recommendation."""
    checks = []
    if composite_pct is not None:
        checks.append((composite_pct >= 0.5, 30, f"composite rank {composite_pct:.0%} of the liquid universe"
                       + (" - bottom 20%, the plan's exit signal" if composite_pct < 0.2 else "")))
    if pd.notna(f.get("above_ma200")):
        checks.append((bool(f["above_ma200"]), 20, "above its 200-day average" if f["above_ma200"] else "below its 200-day average"))
    if pd.notna(f.get("mom_12_1")):
        checks.append((f["mom_12_1"] > 0, 20, f"12-month momentum {f['mom_12_1'] * 100:+.0f}%"))
    if pd.notna(f.get("atr_pctile_250")):
        checks.append((f["atr_pctile_250"] < 0.9, 15, "volatility normal" if f["atr_pctile_250"] < 0.9 else "volatility expanding"))
    stop = avg_price * (1 - stop_pct)
    breached = last_price <= stop
    checks.append((not breached, 15, f"{'below' if breached else 'above'} the plan's stop from your cost ({stop:,.2f})"))
    total = sum(w for _, w, _ in checks)
    score = 100 * sum(w for ok, w, _ in checks if ok) / total if total else float("nan")
    rule_exit = breached or (composite_pct is not None and composite_pct < 0.2)
    # EXIT only on the plan's two tested exit rules; a low score alone asks for a review - its 60 / 35 bands are
    # descriptive and untested, so they never produce an exit on their own
    rec = EXIT if rule_exit else HOLD if score >= 60 else REVIEW
    return {"score": score, "checks": [(ok, txt) for ok, _, txt in checks], "recommendation": rec, "stop": stop}


def risk(book: pd.DataFrame, cov: pd.DataFrame, betas: dict, stops: dict, sectors: dict) -> dict:
    """``cov``: annualised covariance of daily returns over the held names."""
    total = float(book["value"].sum())
    if total <= 0:
        raise BookError("empty book")
    w = book.set_index("tradingsymbol")["value"] / total
    names = [n for n in w.index if n in cov.index]
    ww = w[names].to_numpy()
    vol = float(np.sqrt(ww @ cov.loc[names, names].to_numpy() @ ww)) if names else float("nan")
    beta = float(sum(w[n] * betas.get(n, np.nan) for n in w.index if pd.notna(betas.get(n))))
    at_stop = float(sum(book.loc[i, "value"] * stops.get(book.loc[i, "tradingsymbol"], 0.0) for i in book.index) / total)
    sec = pd.Series({n: sectors.get(n) or "unassigned" for n in w.index})
    by_sector = w.groupby(sec).sum().sort_values(ascending=False)
    return {"value": total, "pnl": float(book["value"].sum() - book["cost"].sum()), "vol": vol, "beta": beta,
            "at_stops": at_stop, "top_sector": (by_sector.index[0], float(by_sector.iloc[0])) if len(by_sector) else None,
            "weights": w.to_dict(), "by_sector": by_sector.to_dict()}


def record_entries(con, bk: pd.DataFrame, pct: dict, day) -> dict:
    """The first day each holding is seen: its composite rank and price then. Never overwritten."""
    for r in bk.itertuples():
        con.execute("INSERT OR IGNORE INTO holding_entry VALUES (?,?,?,?)", [r.tradingsymbol, day, pct.get(r.tradingsymbol), r.last_price])
    return {s: {"first_day": d, "composite_pct": c, "price": p}
            for s, d, c, p in con.execute("SELECT * FROM holding_entry").fetchall()}


def analyse(rcon, bk: pd.DataFrame, asof, *, candidates: pd.DataFrame | None = None, entries: dict | None = None) -> dict:
    """Thesis health per holding, the book's risk, and which candidates add the least risk for their alpha."""
    from ..decisions import plan as P
    from ..graph import exposure as X
    from ..opportunity import engine as E
    from ..opportunity import scanners as S
    from . import marginal as M
    if bk.empty:
        raise BookError("no holdings")
    syms = list(bk["tradingsymbol"])
    cand = list(candidates["ticker"]) if candidates is not None and len(candidates) else []
    f = rcon.execute("""SELECT p.ticker, f.* FROM features f JOIN adjusted_prices p
                        ON p.lineage = f.lineage AND p.business_date = f.business_date
                        WHERE f.business_date = ? AND p.ticker IN (SELECT UNNEST(?))""", [asof, syms]).df().set_index("ticker")
    cs = S.cross_section(rcon, asof)
    cs = cs.assign(z=E.composite(cs).to_numpy())
    pct = dict(zip(cs["ticker"], cs["z"].rank(pct=True)))
    px = rcon.execute("""SELECT ticker, business_date, close_adj FROM adjusted_prices WHERE business_date <= ?
                         AND business_date > ?::DATE - INTERVAL 400 DAY AND close_adj > 0
                         AND ticker IN (SELECT UNNEST(?))""", [asof, asof, syms + cand]).df()
    rets = px.pivot_table(index="business_date", columns="ticker", values="close_adj").pct_change(fill_method=None).tail(250)
    idx = rcon.execute("""SELECT index_name, business_date, close_level FROM index_levels_canonical WHERE variant = 'PRICE'
                          AND business_date <= ? AND business_date > ?::DATE - INTERVAL 400 DAY AND index_name IN (SELECT UNNEST(?))""",
                       [asof, asof, [X.MARKET, *X.SECTORS]]).df().pivot_table(index="business_date", columns="index_name", values="close_level")
    ex, _ = X.build_from_returns(rets, idx.pct_change(fill_method=None).reindex(rets.index))
    betas, sectors = dict(zip(ex["ticker"], ex["beta_market"])), dict(zip(ex["ticker"], ex["home_sector"]))
    cov = rets.cov() * 252
    rows, stops = [], {}
    for r in bk.itertuples():
        fr = f.loc[r.tradingsymbol] if r.tradingsymbol in f.index else pd.Series(dtype=float)
        if isinstance(fr, pd.DataFrame):
            fr = fr.iloc[0]
        vol = float(fr.get("vol_60")) if pd.notna(fr.get("vol_60")) else 0.3
        try:
            stop_pct = P.plan(ticker=r.tradingsymbol, price=float(r.average_price), vol_annual=vol,
                              adv_inr=float(fr.get("adv20") or 1e7), capital_inr=1e6, style="position")["stop_distance"]
        except P.PlanError:
            stop_pct = P.MAX_STOP
        stops[r.tradingsymbol] = stop_pct
        th = thesis_health(fr, avg_price=float(r.average_price), last_price=float(r.last_price), stop_pct=stop_pct,
                           composite_pct=pct.get(r.tradingsymbol))
        rows.append({"symbol": r.tradingsymbol, "qty": r.qty, "avg": r.average_price, "last": r.last_price,
                     "value": r.value, "since_cost": r.since_cost, "sector": sectors.get(r.tradingsymbol),
                     "beta": betas.get(r.tradingsymbol), **th,
                     "reason": "; ".join(t for ok, t in th["checks"] if not ok) or "all checks pass"})
    # the exit brain: six views per holding, against the snapshot taken the first day the holding was seen
    from ..decisions import exit_brain as XB
    sector_w = {}
    tot = float(bk["value"].sum())
    for r in bk.itertuples():
        sector_w[sectors.get(r.tradingsymbol)] = sector_w.get(sectors.get(r.tradingsymbol), 0.0) + r.value / tot
    for h in rows:
        ent = entries.get(h["symbol"]) if entries else None
        fr = f.loc[h["symbol"]] if h["symbol"] in f.index else pd.Series(dtype=float)
        if isinstance(fr, pd.DataFrame):
            fr = fr.iloc[0]
        held = None
        if ent and ent.get("first_day"):
            held = int(rcon.execute("SELECT COUNT(DISTINCT business_date) FROM features WHERE business_date > ? AND business_date <= ?",
                                    [ent["first_day"], asof]).fetchone()[0])
        h["exit"] = XB.decide(composite_now=pct.get(h["symbol"]), composite_entry=(ent or {}).get("composite_pct"),
                              price=float(h["last"]), cost=float(h["avg"]), stop=float(h["stop"]),
                              above_ma200=bool(fr["above_ma200"]) if pd.notna(fr.get("above_ma200")) else None,
                              held_sessions=held, horizon=60,
                              atr_pctile=float(fr["atr_pctile_250"]) if pd.notna(fr.get("atr_pctile_250")) else None,
                              recent_filing=False, weight=float(h["value"]) / tot, sector_weight=sector_w.get(h["sector"], 0.0))
        h["since_entry"] = ent
    names = [n for n in syms if n in cov.index]
    rk = risk(bk[bk["tradingsymbol"].isin(names)] if names else bk, cov, betas, stops, sectors)
    alts = None
    if cand:
        c = candidates[candidates["ticker"].isin(cov.index)].rename(columns={"expected_net": "alpha"})[["ticker", "alpha"]]
        keep = [n for n in cov.index if n in set(names) | set(c["ticker"])]
        if len(c):
            alts = M.scores(c, cov.loc[keep, keep], {n: rk["weights"][n] for n in names})
    return {"holdings": rows, "risk": rk, "alternatives": alts, "composite_pct": pct, "cov": cov,
            "book_inr": dict(zip(bk["tradingsymbol"], bk["value"]))}
