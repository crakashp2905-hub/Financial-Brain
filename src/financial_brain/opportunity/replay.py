"""Walk the opportunity engine through history, one rebalance at a time, and measure what it claimed.

Pre-registered in ``PRE-REGISTERED opportunity engine replay``. At each monthly session the engine sees
only that session's features, decides, and every decision - taken, watched, rejected and why - is
written to opportunity memory. Forward 20-session excess returns are then measured for all of them, so
the **rejections are audited as hard as the trades**.

It checks four things the engine asserts but cannot know about itself:

* is the stated expected alpha **calibrated** against what was realised;
* does the **cost model** matter, i.e. what is left of the gross edge;
* is the composite **better than a single naive signal** it was built from;
* do the **rejection rules** throw away winners.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from . import engine as E
from . import memory as M

STEP = 20          #: sessions between rebalances; equals the horizon so windows do not overlap
RUN_ID = "engine-v1"


class ReplayError(ValueError):
    pass


def sessions(con, start: date, end: date, step: int = STEP) -> list[date]:
    ds = [r[0] for r in con.execute(
        "SELECT DISTINCT business_date FROM features WHERE business_date BETWEEN ? AND ? "
        "ORDER BY 1", [start, end]).fetchall()]
    return ds[::step]


def forward_returns(con, sess: list[date], horizon: int) -> pd.DataFrame:
    """Forward ``horizon``-session return for every (session, lineage), from the price calendar."""
    px = con.execute("""
        SELECT lineage, business_date, close_adj FROM adjusted_prices
        WHERE close_adj > 0 AND business_date >= ?""", [min(sess)]).df()
    wide = px.pivot_table(index="business_date", columns="lineage", values="close_adj")
    idx = {d: i for i, d in enumerate(wide.index)}
    rows = []
    for s in sess:
        i = idx.get(pd.Timestamp(s)) if pd.Timestamp(s) in wide.index else idx.get(s)
        if i is None or i + horizon >= len(wide):
            continue
        r = (wide.iloc[i + horizon] / wide.iloc[i] - 1.0).dropna()
        rows.append(pd.DataFrame({"session": s, "lineage": r.index, "fwd": r.values}))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["session", "lineage", "fwd"])


def run(con, *, start: date, end: date, cfg: E.Config = E.Config(), record: bool = True,
        run_id: str = RUN_ID, naive_signal: str = "mom_12_1") -> dict:
    sess = sessions(con, start, end)
    fwd = forward_returns(con, sess, cfg.horizon)
    fwd_by = {s: g.set_index("lineage")["fwd"] for s, g in fwd.groupby("session")}
    per, pooled = [], []
    for s in sess:
        if s not in fwd_by:
            continue
        try:
            d = E.scan(con, s, cfg=cfg)
        except E.EngineError:
            continue
        f = fwd_by[s]
        d = d[d["lineage"].isin(f.index)].copy()
        if len(d) < 50:
            continue
        d["fwd"] = d["lineage"].map(f)
        d["excess"] = d["fwd"] - d["fwd"].mean()
        d["session"] = s
        if record:
            M.record(con, run_id=run_id, session=s, signal_from=s, rows=E.to_memory_rows(d))
        ic = spearmanr(d["z"], d["excess"]).statistic
        top = d.sort_values("z", ascending=False).head(cfg.top_n)
        nav = d.sort_values(naive_signal, ascending=False).head(cfg.top_n)
        trades = d[d["disposition"] == E.TRADE]
        per.append({
            "session": s, "n": len(d), "trades": len(trades), "ic": ic,
            "top_gross": top["excess"].mean(), "top_cost": top["cost"].mean(),
            "naive_gross": nav["excess"].mean(), "naive_cost": nav["cost"].mean(),
            "trade_net_excess": (trades["excess"] - trades["cost"]).mean() if len(trades) else np.nan,
            "trade_stated": trades["net"].mean() if len(trades) else np.nan})
        pooled.append(d[["session", "lineage", "disposition", "alpha", "net", "excess", "fwd"]])
    if not per:
        raise ReplayError("no session produced a decision")
    return {"per_session": pd.DataFrame(per), "pooled": pd.concat(pooled, ignore_index=True),
            "cfg": cfg}


def t_stat(x: pd.Series) -> float:
    x = x.dropna()
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) > 2 else float("nan")


def summarise(res: dict) -> dict:
    p, pool = res["per_session"], res["pooled"]
    ok = pool.dropna(subset=["alpha", "excess"])
    slope = float(np.polyfit(ok["alpha"], ok["excess"], 1)[0])
    top_net = p["top_gross"] - p["top_cost"]
    naive_net = p["naive_gross"] - p["naive_cost"]
    blocked = pool[pool["disposition"] == E.REJECT_EVENT]
    rest = pool[pool["disposition"] != E.REJECT_EVENT]
    return {
        "sessions": int(len(p)),
        "share_sessions_with_a_trade": float((p["trades"] > 0).mean()),
        "ic_mean": float(p["ic"].mean()), "ic_t": t_stat(p["ic"]),
        "top_gross_per_month": float(p["top_gross"].mean()), "top_gross_t": t_stat(p["top_gross"]),
        "top_cost_per_month": float(p["top_cost"].mean()),
        "top_net_per_month": float(top_net.mean()), "top_net_t": t_stat(top_net),
        "naive_net_per_month": float(naive_net.mean()), "naive_net_t": t_stat(naive_net),
        "composite_minus_naive_net": float((top_net - naive_net).mean()),
        "calibration_slope": slope,
        "trade_net_excess_realised": float(p["trade_net_excess"].mean()),
        "trade_net_stated": float(p["trade_stated"].mean()),
        "event_blocked_abs_excess": float(blocked["excess"].abs().mean()) if len(blocked) else None,
        "event_rest_abs_excess": float(rest["excess"].abs().mean()),
        "event_blocked_mean_excess": float(blocked["excess"].mean()) if len(blocked) else None,
        "event_rest_mean_excess": float(rest["excess"].mean()),
    }
