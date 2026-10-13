"""The edge candidates of ``PRE-REGISTERED edges for every mode`` run on paper in the zoo, passed or failed.

Each returns target weights for the next session (lineages, "NIFTY50"), with the same definitions as the tests in
``evaluation/edges.py``. The verdict each earned is kept beside it (``VERDICT``), so a paper record is never read
as a new discovery. Monthly strategies rebalance on the first session of a month (the test used the last close of
the month before - one session's shift); state between evenings is kept in ``zoo_state``.

    edge_p1_residual_mom      P1 residual momentum, top 20 of 500, monthly
    edge_p2_volmanaged_mom    P2 momentum top 20 scaled by 12% / trailing 6-month vol (cap 1.5x), monthly
    edge_p3_frog_in_pan       P3 momentum top 20 among the continuous-information half, monthly
    edge_s1_no_news_reversal  S1 bottom decile of 5-session excess without material news, every 5 sessions
    edge_s2_reaction_drift    S2 top-fifth results reaction, bought the session after, held 20 sessions
    edge_f1_long_buildup      F1 price up and OI change top quintile, F&O names, every 5 sessions
    edge_f2_short_covering    F2 price up and OI change bottom quintile: short, long Nifty of equal notional
    edge_z1_turn_of_month     Z1 long Nifty from 2 sessions before month end to the 3rd session of the next
    edge_i1_index_intraday    I1 settled each evening from the day's Nifty minute bars (sign of the move to 09:45,
                              position 15:00-15:29), when a Kite token is present
"""
from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd

VERDICT = {"edge_p1_residual_mom": "failed (t -0.3 / +1.8)", "edge_p2_volmanaged_mom": "failed (t +2.3 / +1.2; halves drawdown)",
           "edge_p3_frog_in_pan": "failed (t +3.3 / +1.7)", "edge_s1_no_news_reversal": "failed (t -4.3 / -0.6)",
           "edge_s2_reaction_drift": "failed (t -0.8 / +0.8)", "edge_f1_long_buildup": "failed (t +0.2 / -2.0)",
           "edge_f2_short_covering": "failed (t +0.6 / -0.5)", "edge_z1_turn_of_month": "failed (t +1.3 / +0.9)",
           "edge_i1_index_intraday": "failed (gross 0.0 bp, t -7.5 / -8.1 net)",
           "results_reaction_short": "passed on unseen names (N2: t -2.6 / -4.7); forward test"}
KIND = {k: "edge" for k in VERDICT}


def _first_of_month(z, day: date) -> bool:
    prev = z.execute("SELECT MAX(day) FROM zoo_pnl WHERE day < ?", [day]).fetchone()[0]
    return prev is None or pd.Timestamp(prev).to_period("M") != pd.Timestamp(day).to_period("M")


def _panel(rcon, day: date):
    from ..evaluation import edges as E
    P = E.load(rcon, start=str(pd.Timestamp(day) - pd.DateOffset(years=4))[:10])
    P = {k: v.loc[: pd.Timestamp(day)] for k, v in P.items()}
    return P


def monthly_targets(rcon, day: date) -> dict[str, dict]:
    """P1, P2, P3 targets on ``day``'s close (equal weight; P2 scaled)."""
    from ..evaluation import edges as E
    P = _panel(rcon, day)
    last = P["C"].index[-1]
    out = {}
    mom = E.momentum_scores(P)
    res = E.residual_scores(P)
    idd = E.info_discreteness(P)
    uni = P["U"].loc[last][P["U"].loc[last]].index
    for name, s in (("edge_p1_residual_mom", res.loc[last, uni]),):
        pick = s.dropna().nlargest(20).index
        out[name] = {k: 1.0 / len(pick) for k in pick}
    m = mom.loc[last, uni].dropna()
    low = idd.loc[last, m.index] <= idd.loc[last, uni].median()
    pick = m[low.fillna(False)].nlargest(20).index
    out["edge_p3_frog_in_pan"] = {k: 1.0 / len(pick) for k in pick}
    # P2: the plain momentum book's monthly net history decides the scale
    picks = E.top_picks(P, mom)
    picks[last] = list(m.nlargest(20).index)
    hist = E.monthly_selection(P, picks)["net"]
    vol = hist.tail(6).std(ddof=1) * np.sqrt(12) if len(hist) >= 6 else np.nan
    w = float(min(1.5, 0.12 / vol)) if vol == vol and vol > 0 else 1.0
    top = m.nlargest(20).index
    out["edge_p2_volmanaged_mom"] = {k: w / len(top) for k in top}
    return out


def s1_targets(rcon, day: date, uni: list[str], bars_close: pd.DataFrame) -> dict:
    """S1 on ``day``: no-news losers among ``uni``. ``bars_close``: sessions x lineages, up to ``day``."""
    from ..evaluation import edges as E
    if len(bars_close) < 6:
        return {}
    r5 = (bars_close.iloc[-1] / bars_close.iloc[-6] - 1).reindex(uni).dropna()
    r5 = r5 - r5.mean()
    losers = r5[r5 <= r5.quantile(0.10)].index
    since = bars_close.index[-5]
    news = {r[0] for r in rcon.execute(f"""SELECT DISTINCT p.lineage FROM announcements a
              JOIN (SELECT DISTINCT isin, lineage FROM adjusted_prices WHERE isin IS NOT NULL) p USING (isin)
              WHERE a.event_type IN ({','.join("'" + x + "'" for x in E.NEWS_TYPES)}) AND a.business_date > ?::DATE - INTERVAL 3 DAY
              AND a.business_date <= ? AND p.lineage IN (SELECT UNNEST(?))""", [since, day, list(losers)]).fetchall()}
    pick = [x for x in losers if x not in news]
    return {k: 1.0 / len(pick) for k in pick} if pick else {}


def s2_targets(rcon, day: date, st: dict, sessions: list) -> tuple[dict, dict]:
    """S2: names whose results reaction closed yesterday in the top fifth of the trailing 30 days' reactions are
    bought today, each held 20 sessions."""
    from ..decisions import news_filter as NF
    held = {k: v for k, v in st.get("held", {}).items()}
    re = NF.reactions(rcon, day)
    if len(re) and len(sessions) >= 2:
        yday = pd.Timestamp(sessions[-2])
        for r in re[re["after"] == yday].itertuples():
            peers = re[(re["after"] <= r.after) & (re["after"] > r.after - pd.Timedelta(days=NF.WINDOW_DAYS))]["react"]
            pct = float((peers < r.react).mean() + 0.5 * (peers == r.react).mean())
            if len(peers) >= 5 and pct >= 0.8:
                held[r.lineage] = str(day)
    sess = [str(pd.Timestamp(s).date()) for s in sessions]
    keep = {k: v for k, v in held.items() if v in sess and len(sess) - 1 - sess.index(v) < 20}
    w = {k: 1.0 / len(keep) for k in keep} if keep else {}
    return w, {"held": keep}


def oi_targets(fut, lins_by_symbol: dict, closes: pd.DataFrame, day: date) -> tuple[dict, dict]:
    """F1 / F2 on ``day`` from the futures OI history and spot closes (sessions x lineages)."""
    oi = fut.execute("""SELECT symbol, d, oi FROM futures_daily WHERE d <= ? AND d > ?::DATE - INTERVAL 20 DAY AND oi > 0""",
                     [day, day]).df()
    if oi.empty:
        return {}, {}
    oi["d"] = pd.to_datetime(oi["d"])
    O = oi.pivot(index="d", columns="symbol", values="oi").sort_index()
    if len(O) < 6 or O.index[-1] != pd.Timestamp(day):
        return {}, {}
    d5 = (O.iloc[-1] / O.iloc[-6] - 1)
    rows = []
    for sym, ch in d5.dropna().items():
        lin = lins_by_symbol.get(sym)
        if lin in closes.columns and len(closes) >= 6:
            c = closes[lin]
            if np.isfinite(c.iloc[-1]) and np.isfinite(c.iloc[-6]):
                rows.append((lin, c.iloc[-1] / c.iloc[-6] - 1, ch))
    if len(rows) < 50:
        return {}, {}
    df = pd.DataFrame(rows, columns=["lin", "r", "oi"])
    hi, lo = df["oi"].quantile(0.8), df["oi"].quantile(0.2)
    lb = df[(df["r"] > 0) & (df["oi"] >= hi)]["lin"]
    sc = df[(df["r"] > 0) & (df["oi"] <= lo)]["lin"]
    f1 = {k: 1.0 / len(lb) for k in lb} if len(lb) else {}
    f2 = {k: -1.0 / len(sc) for k in sc} if len(sc) else {}
    if f2:
        f2["NIFTY50"] = 1.0
    return f1, f2


def z1_exposure(day: date) -> float:
    """1 if ``day``'s close starts or continues the turn-of-month window, by weekdays (holidays near a month end
    shift it by a session - stated)."""
    d = pd.Timestamp(day)
    month_days = pd.bdate_range(d.replace(day=1), d + pd.offsets.MonthEnd(0))
    if d in month_days[-3:-1] or d == month_days[-1]:
        return 1.0                                       # from 2 sessions before month end
    first = pd.bdate_range(d.replace(day=1), periods=3)
    return 1.0 if d in first[:2] else 0.0              # held to the 3rd session's close


def i1_return(day: date) -> float | None:
    """I1's net return for ``day`` from Kite's Nifty 50 minute bars (None without a token or bars)."""
    from datetime import datetime, time
    from ..evaluation import edges as E
    try:
        from ..providers import kite_data as kd
        bars = kd.candles(256265, datetime.combine(day, time(9, 15)), datetime.combine(day, time(15, 30)), interval="minute")
        prev = kd.candles(256265, pd.Timestamp(day) - pd.Timedelta(days=7), pd.Timestamp(day) - pd.Timedelta(days=1), interval="day")
    except Exception:                                   # noqa: BLE001 - no token: no I1 tonight
        return None
    if not bars or not prev:
        return None
    b = pd.DataFrame(bars)
    out = E.index_intraday_momentum(b[["ts", "close"]], {pd.Timestamp(day): float(prev[-1]["close"])})
    return float(out["net"].iloc[0]) if len(out) else None


def state(z, s: str) -> dict:
    r = z.execute("SELECT state FROM zoo_state WHERE strategy = ?", [s]).fetchone()
    return json.loads(r[0]) if r else {}


def save(z, s: str, st: dict) -> None:
    z.execute("INSERT OR REPLACE INTO zoo_state VALUES (?, ?)", [s, json.dumps(st, default=str)])


# ------------------------------------------------------------------------------------------------- batch two

VERDICT.update({"edge_w1_reversal_ls": "failed (t -1.2 / -1.4; negative gross)", "edge_w2_52wh_ls": "failed (t -0.0 / -1.3)",
                "edge_w3_momentum_ls": "failed (t -0.0 / +0.5)", "edge_e1_mom_ensemble": "failed (t +0.7 / -0.1)",
                "edge_x1_overnight": "failed (t +5.9 / +1.1: faded since 2022)", "edge_x2_opening_fade": "failed (t -0.5 / -0.1)"})


def ls_targets(closes: pd.DataFrame, fno_lins: list[str], kind: str, frac: float = 0.10) -> dict:
    """W1 (5-session reversal), W2 (nearness to the 52-week high), W3 (12-1 momentum): market-neutral deciles of
    the F&O names, +1 long and -1 short."""
    C = closes[[c for c in fno_lins if c in closes.columns]]
    if len(C) < 253 and kind != "W1":
        return {}
    if kind == "W1":
        sc = -(C.iloc[-1] / C.iloc[-6] - 1)
    elif kind == "W2":
        sc = C.iloc[-1] / C.iloc[-252:].max()
    else:
        sc = C.iloc[-22] / C.iloc[-253] - 1
    sc = sc.dropna()
    if len(sc) < 50:
        return {}
    L = sc[sc >= sc.quantile(1 - frac)].index
    S = sc[sc <= sc.quantile(frac)].index
    w = {k: 1.0 / len(L) for k in L}
    for k in S:
        w[k] = w.get(k, 0.0) - 1.0 / len(S)
    return w


def ensemble_target(rcon, day: date) -> dict:
    from ..evaluation import edges as E
    P = _panel(rcon, day)
    ens = E.ensemble_scores(P)
    last = P["C"].index[-1]
    if last not in ens.index:
        ens.loc[last] = E.ensemble_scores(P).iloc[-1]
    pick = ens.loc[last].dropna().nlargest(20).index
    return {k: 1.0 / len(pick) for k in pick}


def index_day(day: date) -> dict:
    """X1 (yesterday's close to today's open) and X2 (today's opening fade) from Kite's Nifty 50 bars."""
    from datetime import datetime, time
    from ..evaluation import edges as E
    try:
        from ..providers import kite_data as kd
        bars = kd.candles(256265, datetime.combine(day, time(9, 15)), datetime.combine(day, time(15, 30)), interval="minute")
        daily = kd.candles(256265, pd.Timestamp(day) - pd.Timedelta(days=130), pd.Timestamp(day), interval="day")
    except Exception:                                   # noqa: BLE001 - no token: nothing tonight
        return {}
    if not bars or len(daily) < 2:
        return {}
    b = pd.DataFrame(bars)
    out = {}
    dd = pd.DataFrame(daily)
    dd["d"] = pd.to_datetime(dd["ts"]).dt.tz_localize(None).dt.normalize()
    dd = dd.set_index("d")
    today = pd.Timestamp(day)
    if today in dd.index:
        prev_close = float(dd["close"][dd.index < today].iloc[-1])
        out["edge_x1_overnight"] = float(b["open"].iloc[0]) / prev_close - 1 - 2 * E.INDEX_ONE_WAY
    # X2 needs the 09:15-09:30 moves of the previous 60 sessions: from the stored minute history plus today
    try:
        path = "data/edges/nifty50_minute.parquet"
        h = pd.read_parquet(path)
        b["ts"] = pd.to_datetime(b["ts"])
        h = pd.concat([h, b[h.columns.intersection(b.columns)]]).drop_duplicates("ts").sort_values("ts")
        h.to_parquet(path)                              # the stored history grows by a day each evening
        dly = E.index_daily_from_minutes(h)
        ev = E.opening_fade(dly)
        if today in ev.index:
            out["edge_x2_opening_fade"] = float(ev.loc[today, "net"])
        else:
            out["edge_x2_opening_fade"] = 0.0           # no signal today: flat
    except Exception:                                   # noqa: BLE001
        pass
    return out


VERDICT["edge_i2_seasonality_ls"] = "failed on cost (gross +3.6 bp a slot, t +9; net -3% a day)"
HH_PATH = "data/edges/halfhour.parquet"


def i2_day(rcon, day: date, *, lookback: int = 20) -> float | None:
    """I2 for ``day``: every half-hour slot, long the top and short the bottom decile of the names' mean same-slot
    return over the previous ``lookback`` sessions; the day's sum over slots, net of both legs' intraday costs.
    The slot history (``HH_PATH``) starts from the minute-bar table and grows by a day each evening from Kite."""
    from datetime import datetime, time
    from pathlib import Path
    from ..evaluation import edges as E
    if Path(HH_PATH).exists():
        hh = pd.read_parquet(HH_PATH)
    else:
        hh = E.half_hour_returns(rcon)
        hh["r"] = hh["end_px"] / hh["start_px"] - 1
        hh = hh[hh["n"] >= 10][["s", "d", "slot", "r"]]
        hh["d"] = pd.to_datetime(hh["d"])
    today = pd.Timestamp(day)
    if not (hh["d"] == today).any():
        try:
            from ..providers import kite_data as kd
            inst = kd.instruments("NSE")
        except Exception:                               # noqa: BLE001 - no token
            return None
        rows = []
        for sym in sorted(hh["s"].unique()):
            tok = kd.token_for(sym, inst)
            if tok is None:
                continue
            try:
                bars = kd.candles(tok, datetime.combine(day, time(9, 15)), datetime.combine(day, time(15, 30)), interval="minute")
            except Exception:                           # noqa: BLE001 - one name failing leaves the rest
                continue
            if len(bars) < 100:
                continue
            b = pd.DataFrame(bars)
            b["ts"] = pd.to_datetime(b["ts"])
            mins = b["ts"].dt.hour * 60 + b["ts"].dt.minute - 555
            b["slot"] = mins // 30
            b = b[(b["slot"] >= 0) & (b["slot"] <= 12)]
            g = b.groupby("slot").agg(c0=("close", "first"), c1=("close", "last"), n=("close", "size"))
            g["start"] = g["c1"].shift(1).fillna(g["c0"])
            for slot, r in g.iterrows():
                if r["n"] >= 10:
                    rows.append({"s": sym, "d": today, "slot": int(slot), "r": float(r["c1"] / r["start"] - 1)})
        if not rows:
            return None
        hh = pd.concat([hh, pd.DataFrame(rows)], ignore_index=True)
        hh.to_parquet(HH_PATH)
    total, slots = 0.0, 0
    for _slot, g in hh[hh["d"] <= today].groupby("slot"):
        R = g.pivot_table(index="d", columns="s", values="r")
        if today not in R.index:
            continue
        sig = R[R.index < today].tail(lookback)
        if len(sig) < 15:
            continue
        sig = sig.mean()
        r = R.loc[today]
        sig = sig[r.reindex(sig.index).notna()]
        if len(sig) < 100:
            continue
        spread = r[sig[sig >= sig.quantile(0.9)].index].mean() - r[sig[sig <= sig.quantile(0.1)].index].mean()
        total += spread - 2 * E.INTRADAY_RT
        slots += 1
    return total if slots else None


VERDICT["edge_l2_learned_ls"] = "failed (t +0.65 / +2.44; strongest futures result)"


def l2_targets(rcon, day: date, fno_lins: list[str], frac: float = 0.10) -> dict:
    """L2: the learned model's scores for the F&O names, long the top decile and short the bottom, equal gross."""
    from ..evaluation import learned_model as LM
    sc = LM.score_day(rcon, day)
    if sc is None or sc.empty:
        return {}
    s = sc[sc["lineage"].isin(fno_lins)].set_index("lineage")["pred"]
    if len(s) < 50:
        return {}
    L = s[s >= s.quantile(1 - frac)].index
    S = s[s <= s.quantile(frac)].index
    w = {k: 1.0 / len(L) for k in L}
    for k in S:
        w[k] = w.get(k, 0.0) - 1.0 / len(S)
    return w


# ------------------------------------------------------------------------------------------------- ADR-0004 trials

VERDICT.update({"edge_m1_momentum_book": "failed G3 (t +1.5 / +1.2), beat the random-book null; forward test (G5)",
                "edge_f3_short_buildup": "failed (t -1.2 / +0.9, wrong sign 2019-21)"})
KIND.update({"edge_m1_momentum_book": "edge", "edge_f3_short_buildup": "edge"})


def m1_targets(rcon, day: date, st: dict) -> tuple[dict, dict]:
    """M1, rebalanced every 40 sessions (``PRE-REGISTERED cost-aware momentum book``); between rebalances the
    weights drift and are carried as they were set."""
    from ..evaluation import momentum_book as MB
    k = int(st.get("k", 0))
    if k % MB.HOLD == 0 or "w" not in st:
        w, held = MB.targets(rcon, day, st.get("held", []))
        st = {"w": w, "held": held, "set": str(day)}
    st["k"] = k + 1
    return st["w"], st


def f3_targets(fut, lins_by_symbol: dict, closes: pd.DataFrame, day: date) -> dict:
    """F3 on ``day``: short the F&O names in short build-up, long the Nifty for the same notional (as F2 and N2 are
    traded here). Empty when the futures history does not reach ``day``."""
    from ..evaluation import short_buildup as SB
    oi = fut.execute("""SELECT symbol, d, SUM(oi) AS oi FROM futures_daily WHERE d <= ? AND d > ?::DATE - INTERVAL 20 DAY
                        AND oi > 0 GROUP BY 1, 2""", [day, day]).df()
    if oi.empty:
        return {}
    oi["d"] = pd.to_datetime(oi["d"])
    O = oi.pivot(index="d", columns="symbol", values="oi").sort_index()
    if len(O) < SB.K + 1 or O.index[-1] != pd.Timestamp(day):
        return {}
    syms = [s for s in O.columns if lins_by_symbol.get(s) in closes.columns]
    Sp = pd.DataFrame({s: closes[lins_by_symbol[s]] for s in syms})
    Sp.index = pd.to_datetime(Sp.index)
    Sp = Sp.reindex(O.index)
    M = Sp.notna() & O[syms].notna()
    flagged, _ = SB.flags(Sp, O[syms], M, O.index[-1])
    if not flagged:
        return {}
    w = {lins_by_symbol[s]: -1.0 / len(flagged) for s in flagged}
    w["NIFTY50"] = 1.0
    return w
