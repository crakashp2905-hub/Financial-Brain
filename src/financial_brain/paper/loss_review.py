"""Learning from the bad trades - without fitting rules to noise.

Every intraday paper trade records the situation it was taken in (the market's move since the open, the daily
trend, the name's gap, the opening-range width, the minute). This review groups the closed trades by that context
and asks which situations keep losing: per group, net bp a trade with one observation per *day* (trades on one day
share one market), and the share of the total loss the group accounts for.

What it does with the answer is the important part. A losing pattern becomes a **hypothesis**, written to the
``hypotheses`` table as PROPOSED, with the number of groups examined printed beside it - examine thirty groups and
one or two will look bad at t = -2 by chance alone. A hypothesis changes nothing until it is pre-registered as a new
rule version and survives data it was not found on (a held-out backtest, then paper trading as a challenger beside
the current rules). That is the loop: losses -> hypothesis -> out-of-sample test -> new version or discard.

Removing past losers in-sample always looks like a fix; that is why the review never applies one.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

T_FLAG = 2.0
MIN_TRADES = 30
MIN_DAYS = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS hypotheses (
    pattern VARCHAR PRIMARY KEY, first_seen VARCHAR, last_seen VARCHAR, trades INTEGER, days INTEGER,
    mean_bp DOUBLE, t DOUBLE, loss_share DOUBLE, groups_examined INTEGER, status VARCHAR, note VARCHAR);
"""


class ReviewError(ValueError):
    pass


def frame(con, mode: str | None = None) -> pd.DataFrame:
    where = "WHERE exit_ts IS NOT NULL" + (" AND mode = ?" if mode else "")
    df = con.execute(f"""SELECT day, mode, strategy, symbol, side, entry_px * qty AS notional, net_pnl, exit_reason,
                                mkt_ret, daily_up, gap, or_width, entry_minute FROM trades {where}""",
                     [mode] if mode else []).df()
    if df.empty:
        raise ReviewError("no closed trades to review")
    return label(df)


def label(df: pd.DataFrame) -> pd.DataFrame:
    """The context dimensions, each a small set of named situations."""
    d = df.copy()
    d["net_bp"] = d["net_pnl"] / d["notional"] * 1e4
    up = d["side"] == "long"
    d["vs_market_today"] = np.where(d["mkt_ret"].isna(), None,
                                    np.where((d["mkt_ret"] > 0) == up, "with the market", "against the market"))
    d["vs_daily_trend"] = np.where(d["daily_up"].isna(), None,
                                   np.where(d["daily_up"].astype("boolean").fillna(False).to_numpy() == up.to_numpy(),
                                            "with the trend", "against the trend"))
    d["gap_size"] = pd.cut(d["gap"].abs(), [-np.inf, 0.005, 0.015, np.inf], labels=["gap < 0.5%", "gap 0.5-1.5%", "gap > 1.5%"])
    d["range_width"] = pd.cut(d["or_width"], [-np.inf, 0.008, 0.015, np.inf], labels=["range < 0.8%", "range 0.8-1.5%", "range > 1.5%"])
    d["entry_time"] = pd.cut(d["entry_minute"], [-np.inf, 584, 629, np.inf], labels=["09:30-09:44", "09:45-10:29", "10:30-11:00"])
    d["direction"] = d["side"]
    return d


DIMENSIONS = ("vs_market_today", "vs_daily_trend", "gap_size", "range_width", "entry_time", "direction", "strategy")


def review(df: pd.DataFrame, *, t_flag: float = T_FLAG, min_trades: int = MIN_TRADES, min_days: int = MIN_DAYS) -> dict:
    total_loss = float(-df.loc[df["net_pnl"] < 0, "net_pnl"].sum()) or 1.0
    rows = []
    for dim in DIMENSIONS:
        if dim not in df or df[dim].isna().all():
            continue
        for val, g in df.dropna(subset=[dim]).groupby(dim, observed=True):
            per_day = g.groupby("day")["net_bp"].mean()
            if len(g) < min_trades or len(per_day) < min_days:
                continue
            se = per_day.std(ddof=1) / np.sqrt(len(per_day))
            t = float(per_day.mean() / se) if se > 0 else 0.0
            rows.append({"pattern": f"{dim} = {val}", "dimension": dim, "trades": int(len(g)), "days": int(len(per_day)),
                         "mean_bp": float(g["net_bp"].mean()), "t": t, "net_inr": float(g["net_pnl"].sum()),
                         "loss_share": float(-g.loc[g["net_pnl"] < 0, "net_pnl"].sum() / total_loss),
                         "losing": bool(per_day.mean() < 0 and t <= -t_flag)})
    res = pd.DataFrame(rows)
    n = len(res)
    return {"groups_examined": n, "table": res.sort_values("t") if n else res,
            "losing": res[res["losing"]].sort_values("t") if n else res,
            "chance_note": f"{n} groups examined: about {n * 0.023:.1f} would show t <= -2 by chance alone",
            "days": int(df["day"].nunique()), "trades": int(len(df))}


def propose(con, r: dict, *, now: str | None = None) -> int:
    """Write the losing patterns as PROPOSED hypotheses. Existing ones are refreshed, never promoted here."""
    con.execute(SCHEMA)
    now = now or datetime.now().isoformat(timespec="seconds")
    n = 0
    for x in r["losing"].itertuples():
        old = con.execute("SELECT first_seen, status FROM hypotheses WHERE pattern = ?", [x.pattern]).fetchone()
        con.execute("""INSERT OR REPLACE INTO hypotheses VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    [x.pattern, old[0] if old else now, now, x.trades, x.days, x.mean_bp, x.t, x.loss_share,
                     r["groups_examined"], old[1] if old else "PROPOSED",
                     "needs a pre-registered rule version and an out-of-sample test before any rule changes"])
        n += 1
    return n


def render(r: dict) -> str:
    lines = [f"Loss review: {r['trades']} closed trades over {r['days']} day(s). {r['chance_note']}."]
    if r["days"] < MIN_DAYS:
        lines.append(f"Fewer than {MIN_DAYS} days of trades: groups are shown, none is judged.")
    t = r["table"]
    if len(t):
        lines.append(f"\n{'situation':40s} {'trades':>6s} {'days':>5s} {'net bp':>8s} {'t':>6s} {'Rs':>10s} {'of losses':>9s}")
        for x in t.itertuples():
            lines.append(f"{x.pattern:40s} {x.trades:6d} {x.days:5d} {x.mean_bp:+8.1f} {x.t:+6.2f} {x.net_inr:10,.0f} "
                         f"{x.loss_share:9.0%}{'  <- losing pattern (hypothesis)' if x.losing else ''}")
    return "\n".join(lines)
