"""The live paper zoo: every tested daily strategy run on paper each evening, rules frozen as tested.

Pre-registered in ``PRE-REGISTERED live paper zoo``. Each strategy turns the data up to a close into **target weights**
({instrument: weight}; equity lineages, or "NIFTY50"); the next session's close-to-close return is earned on them, and
one-way costs are charged on every change of weight. State a strategy needs between evenings (open pairs, positions,
a martingale's stake) is kept in ``zoo_state``. Stored in ``data/paper_zoo.duckdb``:

    zoo_targets   strategy, day, key, weight
    zoo_pnl       strategy, day, gross, cost, net, bench, holdings
    zoo_state     strategy, state (JSON)
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

PATH = Path("data") / "paper_zoo.duckdb"
COST_LONG, COST_SHORT, COST_INDEX = 0.0015, 0.0005, 0.0005
INDEX = "NIFTY50"
RULES = ("ma_cross_50_200", "ema_ribbon", "macd_cross", "bollinger_reversion", "rsi_exhaustion", "stochastic_cross",
         "atr_breakout", "ichimoku_breakout", "donchian_55_20", "cci_trend", "volume_spike_entry")
KIND = {**{r: "long_only" for r in RULES}, "pairs_distance": "long_short", "pairs_coint": "long_short",
        "pca_statarb": "long_short", "vix_mean_reversion": "index", "scale_trader": "index", "grid": "index",
        "martingale": "index", "anti_martingale": "index", "carry_basis_10": "carry"}
CARRY_RATE = 0.00265 / 2          #: half the cash-and-carry round trip, charged on entry and on exit
CARRY_MIN_BASIS, CARRY_MIN_DAYS, CARRY_MAX = 0.10, 5, 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS zoo_targets (strategy VARCHAR, day DATE, key VARCHAR, weight DOUBLE, PRIMARY KEY (strategy, day, key));
CREATE TABLE IF NOT EXISTS zoo_pnl (strategy VARCHAR, day DATE, gross DOUBLE, cost DOUBLE, net DOUBLE, bench DOUBLE,
                                    holdings INTEGER, PRIMARY KEY (strategy, day));
CREATE TABLE IF NOT EXISTS zoo_state (strategy VARCHAR PRIMARY KEY, state VARCHAR);
"""


def connect(path: Path | None = None):
    path = path or PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(SCHEMA)
    return con


def _state(z, s: str) -> dict:
    r = z.execute("SELECT state FROM zoo_state WHERE strategy = ?", [s]).fetchone()
    return json.loads(r[0]) if r else {}


def _save_state(z, s: str, st: dict) -> None:
    z.execute("INSERT OR REPLACE INTO zoo_state VALUES (?, ?)", [s, json.dumps(st, default=str)])


def settle(z, strategy: str, day: date, prev_day: date | None, rets: dict, bench: float, new: dict) -> dict:
    """Earn ``rets`` (prev close -> ``day`` close) on the previous targets, charge costs on the move to ``new``."""
    old = {}
    if prev_day is not None:
        old = dict(z.execute("SELECT key, weight FROM zoo_targets WHERE strategy = ? AND day = ?", [strategy, prev_day]).fetchall())
    gross = float(sum(w * rets.get(k, 0.0) for k, w in old.items()))
    keys = set(old) | set(new)
    cost = 0.0
    for k in keys:
        dw = new.get(k, 0.0) - old.get(k, 0.0)
        side = old.get(k, 0.0) if abs(old.get(k, 0.0)) > abs(new.get(k, 0.0)) else new.get(k, 0.0)
        rate = CARRY_RATE if k.startswith("CARRY:") else COST_INDEX if k == INDEX else (COST_SHORT if side < 0 else COST_LONG)
        cost += abs(dw) * rate
    z.execute("DELETE FROM zoo_targets WHERE strategy = ? AND day = ?", [strategy, day])
    if new:
        z.executemany("INSERT INTO zoo_targets VALUES (?,?,?,?)", [(strategy, day, k, float(w)) for k, w in new.items() if w])
    row = {"gross": gross if prev_day else 0.0, "cost": cost, "net": (gross if prev_day else 0.0) - cost,
           "bench": bench if prev_day else 0.0, "holdings": int(sum(1 for w in new.values() if w))}
    z.execute("INSERT OR REPLACE INTO zoo_pnl VALUES (?,?,?,?,?,?,?)", [strategy, day, *row.values()])
    return row


# ------------------------------------------------------------------------------------------------- target makers

def rule_targets(bars: dict[str, pd.DataFrame], day: date) -> dict[str, dict]:
    """Every technical rule's equal-weighted longs among ``bars`` (lineage -> history up to ``day``)."""
    from ..evaluation import rule_systems as RS
    longs = {r: [] for r in RULES}
    for lin, b in bars.items():
        if len(b) < 210 or b["d"].iloc[-1] != day:
            continue
        for name, pos in RS.rules(b.reset_index(drop=True)).items():
            if name in longs and pos.iloc[-1] == 1:
                longs[name].append(lin)
    return {r: {lin: 1.0 / len(v) for lin in v} for r, v in longs.items() if v} | {r: {} for r, v in longs.items() if not v}


def pairs_targets(px: pd.DataFrame, st: dict, *, coint: bool, formation: int = 250, trading: int = 125,
                  n_pairs: int = 20, entry: float = 2.0, exit_: float = 0.5) -> tuple[dict, dict]:
    """``px``: closes (sessions x names) up to today. Re-forms the pairs every ``trading`` sessions."""
    from ..evaluation import arbitrage as AR
    today = len(px) - 1
    if not st or today - st.get("formed_at", -10**9) >= trading or st.get("n_rows") is None:
        f = px.iloc[-formation - 1:-1].dropna(axis=1)
        nf = (f / f.iloc[0]).to_numpy()
        sq = (nf ** 2).sum(0)
        ssd = sq[:, None] + sq[None, :] - 2 * nf.T @ nf
        iu = np.triu_indices_from(ssd, 1)
        pairs = []
        for k in np.argsort(ssd[iu])[: (400 if coint else n_pairs)]:
            i, j = f.columns[iu[0][k]], f.columns[iu[1][k]]
            if coint:
                li, lj = np.log(f[i].to_numpy()), np.log(f[j].to_numpy())
                X = np.column_stack([np.ones(len(lj)), lj])
                b, *_ = np.linalg.lstsq(X, li, rcond=None)
                e = li - X @ b
                t = AR.adf_t(e)
                if t < AR.ADF_5PCT_EG:
                    pairs.append({"i": i, "j": j, "a": float(b[0]), "b": float(b[1]), "mu": float(e.mean()), "sd": float(e.std()), "t": t, "pos": 0})
            else:
                pairs.append({"i": i, "j": j, "f0i": float(f[i].iloc[0]), "f0j": float(f[j].iloc[0]),
                              "sd": float(np.std(nf[:, iu[0][k]] - nf[:, iu[1][k]])), "pos": 0})
        if coint:
            pairs = sorted(pairs, key=lambda p: p["t"])[:n_pairs]
        st = {"formed_at": today, "n_rows": len(px), "pairs": pairs}
    w = {}
    live = [p for p in st["pairs"] if p["i"] in px.columns and p["j"] in px.columns]
    for p in live:
        pi, pj = px[p["i"]].iloc[-1], px[p["j"]].iloc[-1]
        if not (np.isfinite(pi) and np.isfinite(pj)):
            continue
        if coint:
            z_ = (np.log(pi) - (p["a"] + p["b"] * np.log(pj)) - p["mu"]) / p["sd"]
            ex = exit_
        else:
            z_ = (pi / p["f0i"] - pj / p["f0j"]) / p["sd"]
            ex = 1e-9
        pos = p["pos"]
        if pos == 0 and z_ > entry:
            pos = -1
        elif pos == 0 and z_ < -entry:
            pos = 1
        elif pos != 0 and (abs(z_) < ex or np.sign(z_) == pos):
            pos = 0
        p["pos"] = pos
        if pos:
            hb = p.get("b", 1.0)
            wi, wj = pos / (1 + abs(hb)), -pos * hb / (1 + abs(hb))
            w[p["i"]] = w.get(p["i"], 0.0) + wi / max(len(live), 1)
            w[p["j"]] = w.get(p["j"], 0.0) + wj / max(len(live), 1)
    return w, st


def pca_targets(rets: pd.DataFrame, st: dict, *, window: int = 250, k: int = 15, ou: int = 60,
                entry: float = 1.25, exit_: float = 0.5) -> tuple[dict, dict]:
    W = rets.iloc[-window:].dropna(axis=1)
    X = W.to_numpy()
    mu, sd = X.mean(0), X.std(0) + 1e-12
    Z = (X - mu) / sd
    _, _, Vt = np.linalg.svd(Z, full_matrices=False)
    F = Z @ Vt[:k].T
    B, *_ = np.linalg.lstsq(F, X, rcond=None)
    cum = np.cumsum((X - F @ B)[-ou:], axis=0)
    x0, x1 = cum[:-1], cum[1:]
    m0, m1 = x0.mean(0), x1.mean(0)
    b = ((x0 - m0) * (x1 - m1)).sum(0) / (((x0 - m0) ** 2).sum(0) + 1e-18)
    a = m1 - b * m0
    good = (b > 0) & (b < 1)
    var_eq = np.where(good, (x1 - (a + b * x0)).var(0) / np.where(good, 1 - b * b, 1), 0.0)
    m_eq = np.where(good, a / np.where(good, 1 - b, 1), 0.0)
    s = np.where(good & (var_eq > 0), (cum[-1] - m_eq) / np.sqrt(np.where(var_eq > 0, var_eq, 1)), 0.0)
    pos = st.get("pos", {})
    for name, sc in zip(W.columns, s):
        p = pos.get(name, 0)
        p = -1 if sc > entry else 1 if sc < -entry else 0 if abs(sc) < exit_ else p
        pos[name] = p
    pos = {k_: v for k_, v in pos.items() if v and k_ in W.columns}
    n = max(len(pos), 1)
    return {k_: v / n for k_, v in pos.items()}, {"pos": pos}


def index_targets(nifty: pd.Series, vix: pd.Series, z, day: date) -> dict[str, float]:
    """Exposures to the Nifty for VIX mean reversion, scale-trader, grid, martingale and anti-martingale."""
    from ..evaluation import rule_systems as RS
    out = {}
    c = nifty.to_numpy()
    st = _state(z, "vix_mean_reversion")
    hi = vix.iloc[-1] > vix.iloc[-250:].quantile(0.9)
    left = 20 if hi else max(st.get("left", 0) - 1, 0)
    _save_state(z, "vix_mean_reversion", {"left": left})
    out["vix_mean_reversion"] = 1.0 if left else 0.0
    out["scale_trader"] = float(RS.scale_trader(c)[-1])
    out["grid"] = float(RS.grid(c)[-1])
    r_today = float(c[-1] / c[-2] - 1)
    for name, anti in (("martingale", False), ("anti_martingale", True)):
        st = _state(z, name) or {"equity": 1.0, "stake": 0.02, "ruined": False, "last": None}
        if st["last"] is not None and str(st["last"]) != str(day) and not st["ruined"]:
            pnl = st["stake"] * r_today
            st["equity"] += pnl
            win = pnl > 0
            st["stake"] = (0.02 if win else st["stake"] * 2) if not anti else min(st["stake"] * 2 if win else 0.02, st["equity"])
            if st["stake"] > st["equity"]:
                st["ruined"] = True
        st["last"] = str(day)
        _save_state(z, name, st)
        out[name] = 0.0 if st["ruined"] else st["stake"] / max(st["equity"], 1e-9)
    return out


def carry_targets(snap: pd.DataFrame, st: dict, day: date) -> tuple[dict, dict]:
    """``snap``: today's F&O snapshot (symbol, spot, fut, fut_expiry, basis_annualised). Positions held to expiry; new
    ones where the annualised basis exceeds 10% with at least 5 days left, up to 10 names."""
    pos = {k: v for k, v in (st.get("pos") or {}).items() if str(v) > str(day)}          # past expiry: settled, gone
    for r in snap.sort_values("basis_annualised", ascending=False).itertuples():
        if len(pos) >= CARRY_MAX:
            break
        if r.symbol in pos or r.basis_annualised is None or not np.isfinite(r.basis_annualised):
            continue
        left = (pd.Timestamp(r.fut_expiry).date() - day).days
        if r.basis_annualised > CARRY_MIN_BASIS and left >= CARRY_MIN_DAYS:
            pos[r.symbol] = str(r.fut_expiry)
    n = max(len(pos), 1)
    return {f"CARRY:{s_}": 1.0 / n for s_ in pos}, {"pos": pos}


def carry_returns(prev: pd.DataFrame, today: pd.DataFrame) -> dict:
    """Per symbol: (change in spot - change in the same future) / previous spot."""
    m = prev.merge(today, on="symbol", suffixes=("_p", "_t"))
    m = m[m["fut_expiry_p"] == m["fut_expiry_t"]]                    # same contract; a roll is an exit, not a return
    return {f"CARRY:{r.symbol}": float(((r.spot_t - r.spot_p) - (r.fut_t - r.fut_p)) / r.spot_p) for r in m.itertuples()}


def summary(z) -> pd.DataFrame:
    df = z.execute("SELECT * FROM zoo_pnl ORDER BY strategy, day").df()
    if df.empty:
        return df
    g = df.groupby("strategy")
    out = pd.DataFrame({"days": g.size() - 1, "net_total": g["net"].sum(), "bench_total": g["bench"].sum(),
                        "last_net": g["net"].last(), "holdings": g["holdings"].last()})
    out["vs_bench"] = out["net_total"] - out["bench_total"]
    out["kind"] = [KIND.get(s, "book") for s in out.index]
    return out.sort_values("vs_bench", ascending=False)


def run(rcon, z, *, day: date | None = None, fno_symbols: list[str] | None = None,
        fno_snap: pd.DataFrame | None = None) -> dict:
    """One evening: settle yesterday's targets at today's close, then set today's. Idempotent per day."""
    from ..features import technical as TE
    day = day or pd.Timestamp(rcon.execute("SELECT MAX(business_date) FROM features").fetchone()[0]).date()
    done = {r[0] for r in z.execute("SELECT strategy FROM zoo_pnl WHERE day = ?", [day]).fetchall()}
    prev = z.execute("SELECT MAX(day) FROM zoo_pnl WHERE day < ?", [day]).fetchone()[0]
    prev = pd.Timestamp(prev).date() if prev is not None else None
    uni = [r[0] for r in rcon.execute("""SELECT lineage FROM features WHERE business_date = ? AND adv20 > 0
                                         ORDER BY adv20 DESC LIMIT 500""", [day]).fetchall()]
    rcon.execute(TE.BARS_SQL)
    bars = rcon.execute("""SELECT lineage, d, o, h, l, c, v FROM _tbars WHERE lineage IN (SELECT UNNEST(?))
                           AND d <= ? AND d > ?::DATE - INTERVAL 450 DAY ORDER BY lineage, d""", [uni, day, day]).df()
    bars["d"] = pd.to_datetime(bars["d"]).dt.date
    by = {lin: g.reset_index(drop=True) for lin, g in bars.groupby("lineage")}
    rets = {}
    if prev is not None:
        px2 = rcon.execute("""SELECT lineage, business_date, close_adj FROM adjusted_prices WHERE business_date IN (?, ?)
                              AND close_adj > 0""", [prev, day]).df()
        p2 = px2.pivot_table(index="lineage", columns="business_date", values="close_adj")
        if p2.shape[1] == 2:
            rets = (p2.iloc[:, 1] / p2.iloc[:, 0] - 1).dropna().to_dict()
    idx = rcon.execute("""SELECT index_name, business_date, close_level FROM index_levels_canonical WHERE variant = 'PRICE'
                          AND index_name IN ('Nifty 50', 'India VIX') AND business_date <= ?""", [day]).df()
    lv = idx.pivot_table(index="business_date", columns="index_name", values="close_level").dropna().sort_index()
    lv.index = [pd.Timestamp(x).date() for x in lv.index]
    if prev is not None and prev in lv.index and day in lv.index:
        rets[INDEX] = float(lv.loc[day, "Nifty 50"] / lv.loc[prev, "Nifty 50"] - 1)
    ubench = float(np.mean([rets[l] for l in uni if l in rets])) if rets else 0.0
    out = {}
    for name, w in rule_targets(by, day).items():
        if name not in done:
            out[name] = settle(z, name, day, prev, rets, ubench, w)
    if fno_symbols:
        tick = rcon.execute("""SELECT ticker, lineage FROM adjusted_prices WHERE business_date = ? AND ticker IN (SELECT UNNEST(?))""",
                            [day, fno_symbols]).fetchall()
        lins = [l for _, l in tick]
        fx = rcon.execute("""SELECT lineage, business_date, close_adj FROM adjusted_prices WHERE lineage IN (SELECT UNNEST(?))
                             AND business_date <= ? AND business_date > ?::DATE - INTERVAL 450 DAY AND close_adj > 0""",
                          [lins, day, day]).df()
        P = fx.pivot_table(index="business_date", columns="lineage", values="close_adj").sort_index()
        for name, coint in (("pairs_distance", False), ("pairs_coint", True)):
            if name in done or len(P) < 260:
                continue
            w, st = pairs_targets(P, _state(z, name), coint=coint)
            _save_state(z, name, st)
            out[name] = settle(z, name, day, prev, rets, 0.0, w)
        if "pca_statarb" not in done and len(P) > 260:
            w, st = pca_targets(P.pct_change(fill_method=None).iloc[1:], _state(z, "pca_statarb"))
            _save_state(z, "pca_statarb", st)
            out["pca_statarb"] = settle(z, "pca_statarb", day, prev, rets, 0.0, w)
    if day in lv.index and len(lv) > 260:
        nb = rets.get(INDEX, 0.0)
        for name, e in index_targets(lv["Nifty 50"], lv["India VIX"], z, day).items():
            if name not in done:
                out[name] = settle(z, name, day, prev, rets, nb, {INDEX: e} if e else {})
    if fno_snap is not None and len(fno_snap) and "carry_basis_10" not in done:
        today_s = fno_snap[fno_snap["day"].astype(str) == str(day)]
        prev_s = fno_snap[fno_snap["day"].astype(str) == str(prev)] if prev is not None else fno_snap.iloc[0:0]
        if len(today_s):
            crets = carry_returns(prev_s, today_s) if len(prev_s) else {}
            w, st = carry_targets(today_s, _state(z, "carry_basis_10"), day)
            _save_state(z, "carry_basis_10", st)
            rf_day = 0.065 * ((day - prev).days if prev else 0) / 365
            out["carry_basis_10"] = settle(z, "carry_basis_10", day, prev, {**rets, **crets}, rf_day, w)
    return {"day": day, "prev": prev, "settled": len(out)}
