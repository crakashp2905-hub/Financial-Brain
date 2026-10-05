"""The 09:30 board: the five best trades available right now, the evidence for each, where each is wrong, how much
capital each gets - and, afterwards, how they did against everything else that could have been traded.

Built once per session from two inputs:

* the **daily view**: the opportunity engine's cross-section as of the latest feature date (composite score,
  expected 20-session alpha, sized round-trip cost, net alpha), and
* the **live view at 09:30**: each name's price, open, opening range and gap from Kite's quote.

Every name considered is ranked and **recorded**, not only the top five. The record is resolved later: the same
day's close (from the session's last quotes) and 1, 5 and 20 sessions on (from the price archive). The board's
scorecard then asks the only question that matters for a ranking: did the names it put on top do better than
the ones it passed over? A board that cannot beat its own leftovers has no skill, whatever it says about each
name.

## Two different claims on one board

    position   the daily signal: hold for months, sized by the plan's position rules, evidence WEAK_IN_SAMPLE.
               "Clears" only if expected 20-session net alpha is above the engine's hurdle.
    intraday   the morning setups (a 1.5% gap, an opening-range breakout pending), evidence NONE_DEMONSTRATED:
               listed so the record shows what was live, never ranked as a trade, because the backtest says
               they lose after costs.

On most mornings the honest answer is "nothing clears costs", and the board says that, then still lists the
five best by expected net alpha as **watch**, so their outcomes are recorded and the claim can be checked.
Information, not advice; nothing here is sent anywhere.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..decisions import plan as P
from ..opportunity import engine as E

TOP_N = 5
GAP_MIN = 0.015
SCHEMA = """
CREATE TABLE IF NOT EXISTS board (
    day VARCHAR, mode VARCHAR, symbol VARCHAR, rank INTEGER, top BOOLEAN, clears BOOLEAN, price DOUBLE,
    gap DOUBLE, z DOUBLE, alpha_20d DOUBLE, cost DOUBLE, net_20d DOUBLE, setups VARCHAR, stop DOUBLE,
    shares INTEGER, features_as_of VARCHAR, ret_close DOUBLE, ret_1d DOUBLE, ret_5d DOUBLE, ret_20d DOUBLE,
    PRIMARY KEY (day, mode, symbol));
"""


class BoardError(ValueError):
    pass


def build(quotes: dict, daily: pd.DataFrame, *, capital: float = 1_000_000.0, top_n: int = TOP_N,
          features_as_of=None) -> pd.DataFrame:
    """``quotes``: {symbol: {price, open, high, low, prev_close}} at 09:30. ``daily``: engine decisions (one row
    per name, with ticker, z, alpha, cost, net, vol_60, adv20)."""
    if daily is None or daily.empty:
        raise BoardError("no daily cross-section to rank")
    d = daily.dropna(subset=["ticker"]).drop_duplicates("ticker").set_index("ticker")
    rows = []
    for sym, q in quotes.items():
        if sym not in d.index or not q.get("price"):
            continue
        r = d.loc[sym]
        price = float(q["price"])
        prev = q.get("prev_close")
        gap = (q["open"] / prev - 1.0) if q.get("open") and prev else np.nan
        setups = []
        if np.isfinite(gap) and abs(gap) >= GAP_MIN:
            setups.append("GAP_UP" if gap > 0 else "GAP_DOWN")
        if q.get("high") and q.get("low") and q["low"] < price < q["high"]:
            setups.append("ORB_PENDING")
        p = P.plan(ticker=sym, price=price, vol_annual=float(r["vol_60"]), adv_inr=float(r["adv20"]),
                   capital_inr=capital, style="position", bucket="large")
        rows.append({"symbol": sym, "price": price, "gap": gap, "z": float(r["z"]), "alpha_20d": float(r["alpha"]),
                     "cost": float(r["cost"]), "net_20d": float(r["net"]), "setups": ",".join(setups),
                     "stop": p["stop_price"], "shares": p["shares"], "plan_status": p["status"],
                     "type": str(r["type"]) if "type" in r and pd.notna(r["type"]) else "composite",
                     "horizon": int(r["horizon"]) if "horizon" in r and pd.notna(r["horizon"]) else 20,
                     "evidence": str(r["evidence"]) if "evidence" in r and pd.notna(r["evidence"]) else "IN_SAMPLE"})
    if not rows:
        raise BoardError("no quoted name is in the daily cross-section")
    b = pd.DataFrame(rows).sort_values("net_20d", ascending=False).reset_index(drop=True)
    b["rank"] = np.arange(1, len(b) + 1)
    b["top"] = b["rank"] <= top_n
    b["clears"] = b["net_20d"] >= E.MIN_NET
    b["features_as_of"] = str(features_as_of) if features_as_of else None
    return b


def render(b: pd.DataFrame, day: str) -> str:
    top = b[b["top"]]
    n_clear = int(b["clears"].sum())
    head = (f"## 09:30 board, {day} - {len(b)} names considered, {n_clear} clear costs"
            + (" (daily view as of " + str(b['features_as_of'].iloc[0]) + ")" if b["features_as_of"].iloc[0] else ""))
    lines = [head, ""]
    if n_clear == 0:
        lines.append("**Nothing clears costs today.** The five best by expected net alpha are listed as WATCH so their "
                     "outcomes are recorded; none is a trade.")
    lines += ["", "| # | Stock | Price | Opportunity (horizon) | Evidence | Exp. alpha | Cost | Net | Verdict | Invalidation (stop) | Position size | Setups at 09:30 |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in top.itertuples():
        verdict = "TRADE (weak, in-sample evidence)" if r.clears else "WATCH"
        lines.append(f"| {r.rank} | {r.symbol} | {r.price:,.1f} | {r.type} ({r.horizon}d) | {r.evidence} | {r.alpha_20d:+.2%} | "
                     f"{r.cost:.2%} | {r.net_20d:+.2%} | {verdict} | {r.stop:,.1f} | {r.shares} sh | {r.setups or '-'} |")
    gaps = b[b["setups"].str.contains("GAP", na=False)]
    lines += ["", f"Intraday setups live at 09:30: {len(gaps)} gaps of 1.5%+ and "
              f"{int(b['setups'].str.contains('ORB_PENDING', na=False).sum())} pending opening-range breakouts. "
              "Evidence for both: none demonstrated (they lose after costs in the backtest); the paper engine trades "
              "them only to measure that live.", "",
              "Information, not advice. Nothing here is sent to a broker."]
    return "\n".join(lines)


def save(con, b: pd.DataFrame, day: str, mode: str) -> int:
    con.execute(SCHEMA)
    for name, typ in (("type", "VARCHAR"), ("horizon", "INTEGER"), ("evidence", "VARCHAR")):
        con.execute(f"ALTER TABLE board ADD COLUMN IF NOT EXISTS {name} {typ}")
    for r in b.itertuples():
        con.execute("""INSERT OR REPLACE INTO board (day, mode, symbol, rank, top, clears, price, gap, z, alpha_20d, cost,
                       net_20d, setups, stop, shares, features_as_of) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [day, mode, r.symbol, int(r.rank), bool(r.top), bool(r.clears), float(r.price),
                     None if not np.isfinite(r.gap) else float(r.gap), float(r.z), float(r.alpha_20d), float(r.cost),
                     float(r.net_20d), r.setups, float(r.stop), int(r.shares), r.features_as_of])
        con.execute("UPDATE board SET type = ?, horizon = ?, evidence = ? WHERE day = ? AND mode = ? AND symbol = ?",
                    [getattr(r, "type", "composite"), int(getattr(r, "horizon", 20)), getattr(r, "evidence", None),
                     day, mode, r.symbol])
    return len(b)


def resolve_close(con, day: str, mode: str, closes: dict[str, float]) -> int:
    """Same-day outcome: 09:30 price to the session's last price, for every name considered."""
    n = 0
    for sym, px in closes.items():
        if px:
            n += con.execute("""UPDATE board SET ret_close = ? / price - 1 WHERE day = ? AND mode = ? AND symbol = ?
                                RETURNING 1""", [float(px), day, mode, sym]).fetchall().__len__()
    return n


def resolve_horizons(con, research_con, horizons=(1, 5, 20)) -> int:
    """Multi-day outcomes from the price archive, filled in once those sessions exist."""
    con.execute(SCHEMA)
    open_rows = con.execute("SELECT DISTINCT day FROM board WHERE ret_20d IS NULL").fetchall()
    n = 0
    for (day,) in open_rows:
        cal = [r[0] for r in research_con.execute(
            "SELECT DISTINCT business_date FROM adjusted_prices WHERE business_date >= ? ORDER BY 1 LIMIT 21",
            [day]).fetchall()]
        if not cal or str(cal[0]) != day:
            continue
        for h in horizons:
            if len(cal) <= h:
                continue
            px = dict(research_con.execute("""
                SELECT a.ticker, b.close_adj / a.close_adj - 1 FROM adjusted_prices a
                JOIN adjusted_prices b ON b.lineage = a.lineage AND b.business_date = ?
                WHERE a.business_date = ? AND a.close_adj > 0""", [cal[h], cal[0]]).fetchall())
            for (sym,) in con.execute("SELECT symbol FROM board WHERE day = ?", [day]).fetchall():
                if sym in px:
                    con.execute(f"UPDATE board SET ret_{h}d = ? WHERE day = ? AND symbol = ?", [px[sym], day, sym])
                    n += 1
    return n


def scorecard(con) -> list[dict]:
    """Did the top of the board beat the rest? Per horizon: mean return of the top five, of everything else, the
    spread, and a t-statistic with one observation per day."""
    con.execute(SCHEMA)
    out = []
    for col, label in (("ret_close", "same day (09:30 to close)"), ("ret_1d", "1 session"),
                       ("ret_5d", "5 sessions"), ("ret_20d", "20 sessions")):
        df = con.execute(f"SELECT day, top, {col} FROM board WHERE {col} IS NOT NULL").df()
        if df.empty:
            out.append({"horizon": label, "days": 0})
            continue
        g = df.groupby(["day", "top"])[col].mean().unstack()
        if True not in g.columns or False not in g.columns:
            out.append({"horizon": label, "days": 0})
            continue
        spread = (g[True] - g[False]).dropna()
        t = float(spread.mean() / (spread.std(ddof=1) / np.sqrt(len(spread)))) if len(spread) > 2 else float("nan")
        out.append({"horizon": label, "days": int(len(spread)), "top": float(g[True].mean()),
                    "rest": float(g[False].mean()), "spread": float(spread.mean()), "t": t})
    return out
