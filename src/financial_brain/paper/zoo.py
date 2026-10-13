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
        "martingale": "index", "anti_martingale": "index", "carry_basis_10": "carry", "sharpe_allocator": "allocator",
        "results_reaction_short": "long_short",
        **{k: "edge" for k in ("edge_p1_residual_mom", "edge_p2_volmanaged_mom", "edge_p3_frog_in_pan", "edge_s1_no_news_reversal",
                               "edge_s2_reaction_drift", "edge_f1_long_buildup", "edge_f2_short_covering", "edge_z1_turn_of_month",
                               "edge_i1_index_intraday", "edge_w1_reversal_ls", "edge_w2_52wh_ls", "edge_w3_momentum_ls",
                               "edge_e1_mom_ensemble", "edge_x1_overnight", "edge_x2_opening_fade", "edge_i2_seasonality_ls", "edge_l2_learned_ls",
                               "edge_m1_momentum_book", "edge_f3_short_buildup", "edge_eap_announcement_premium",
                               "edge_eapr_announcement_short", "edge_bab_futures",
                               "edge_d1_delivery_ls")},
        **{f"edge_k7_{s_}": "edge" for s_ in ("bull_marubozu", "hammer", "bull_engulf", "morning_star", "sweep_lows", "bos_up",
                                             "fvg_bull_retest", "gap_up_unfilled", "high55_volume", "bear_engulf",
                                             "shooting_star", "evening_star", "sweep_highs", "bos_down", "fvg_bear_retest",
                                             "gap_down_unfilled", "low55_volume")}}
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


# ------------------------------------------------------------------------------------------------- sharpe allocator

#: the historical panel's strategies that run in this zoo (panel name -> zoo name). The stock-selection books
#: (momentum, learned, rankings) live in paper/book.py, not here, so the live allocator splits over these only.
PANEL = Path("data") / "strategy_monthly_panel.parquet"
ALLOCATOR = "sharpe_allocator"
ALLOC_MAP = {**{f"rule: {r}": r for r in RULES}, "pairs by distance (long-short)": "pairs_distance",
             "pca stat-arb (long-short)": "pca_statarb", "nifty: VIX mean reversion": "vix_mean_reversion",
             "nifty: scale-trader": "scale_trader", "nifty: grid": "grid", "carry: basis > 10%": "carry_basis_10"}
ALLOC_COST, RF = 0.003, 0.065


def _alloc_nets(z, day: date | None = None) -> pd.DataFrame:
    """Each zoo strategy's daily net on the same footing as the historical panel: cash interest on the idle share
    for index, long-short and carry books (as in their tested series), none for the long-only rules."""
    q = "SELECT p.strategy, p.day, p.net FROM zoo_pnl p WHERE p.strategy IN (SELECT UNNEST(?))" + (" AND p.day <= ?" if day else "")
    df = z.execute(q + " ORDER BY day", [list(ALLOC_MAP.values())] + ([day] if day else [])).df()
    if df.empty:
        return df
    ex = z.execute("SELECT strategy, day, SUM(ABS(weight)) AS gross_w FROM zoo_targets GROUP BY 1, 2").df()
    df = df.sort_values(["strategy", "day"])
    df["prev"] = df.groupby("strategy")["day"].shift()
    df = df.merge(ex.rename(columns={"day": "prev"}), on=["strategy", "prev"], how="left")
    days = (pd.to_datetime(df["day"]) - pd.to_datetime(df["prev"])).dt.days.fillna(0)
    rf_day = RF * days / 365
    kind = df["strategy"].map(KIND)
    idle = np.where(kind == "long_short", 1.0, np.where(kind == "long_only", 0.0, (1 - df["gross_w"].fillna(0)).clip(lower=0)))
    df["adj"] = np.where(df["prev"].isna(), 0.0, df["net"] + idle * rf_day)
    return df


def allocator_weights(z, day: date, panel: pd.DataFrame | None = None) -> dict[str, float]:
    """This month's weights: proportional to each zoo strategy's trailing 36-month Sharpe (positive only), from the
    historical panel extended by the zoo's own complete live months - set only from months before ``day``'s."""
    from ..evaluation import performance as PF
    if panel is None:
        if not PANEL.exists():
            return {}
        panel = pd.read_parquet(PANEL)
    P = panel[[c for c in ALLOC_MAP if c in panel.columns]].rename(columns=ALLOC_MAP)
    month = pd.Period(pd.Timestamp(day), "M")
    live = _alloc_nets(z, day)
    if len(live):
        live["m"] = pd.PeriodIndex(pd.to_datetime(live["day"]), freq="M")
        g = live[live["m"] < month].groupby(["m", "strategy"])
        lm = g["adj"].apply(lambda x: float(np.prod(1 + x) - 1)).unstack()
        lm = lm[g.size().unstack().min(axis=1).reindex(lm.index).fillna(0) >= 15]      # complete months only
        P = pd.concat([P[P.index < (lm.index.min() if len(lm) else month)], lm]).sort_index()
    P = P[P.index < month]
    P.loc[month] = 0.0                                                  # a row to set weights for; its returns unused
    A = PF.sharpe_allocation(P, rf=RF, turnover_cost=ALLOC_COST)
    if A.empty or A.index[-1] != month:
        return {}
    w = A.iloc[-1][[c for c in A.columns if c.startswith("w_")]]
    return {k[2:]: float(v) for k, v in w.items() if v > 0}


def run_allocator(z, day: date, prev: date | None, panel: pd.DataFrame | None = None) -> dict | None:
    """Settle the Sharpe allocator after the strategies it holds have settled ``day``: it earns its weights times
    their nets, cash interest on what it leaves idle, and 0.3% on the share it moves at each month's rebalance."""
    st = _state(z, ALLOCATOR)
    month = str(pd.Period(pd.Timestamp(day), "M"))
    old = st.get("w", {})
    w = old
    if st.get("month") != month:
        w = allocator_weights(z, day, panel)
        if not w and old:
            w = old                                                     # no panel: keep last month's weights
    nets = _alloc_nets(z, day)
    today = nets[nets["day"].astype(str) == str(day)].set_index("strategy")["adj"] if len(nets) else pd.Series(dtype=float)
    days = (day - prev).days if prev else 0
    gross = float(sum(v * today.get(k, 0.0) for k, v in old.items())) + (1 - sum(old.values())) * RF * days / 365 if prev and old else 0.0
    cost = sum(abs(w.get(k, 0.0) - old.get(k, 0.0)) for k in set(w) | set(old)) * ALLOC_COST
    bench = float(today.mean()) if prev and len(today) else 0.0         # equal weight across the same strategies
    row = {"gross": gross, "cost": cost, "net": gross - cost, "bench": bench, "holdings": int(sum(1 for v in w.values() if v))}
    z.execute("INSERT OR REPLACE INTO zoo_pnl VALUES (?,?,?,?,?,?,?)", [ALLOCATOR, day, *row.values()])
    z.execute("DELETE FROM zoo_targets WHERE strategy = ? AND day = ?", [ALLOCATOR, day])
    if w:
        z.executemany("INSERT INTO zoo_targets VALUES (?,?,?,?)", [(ALLOCATOR, day, k, v) for k, v in w.items()])
    _save_state(z, ALLOCATOR, {"month": month, "w": w})
    return row


def edge_strategies(rcon, z, day, prev, done, uni, by, rets, ubench, fno_symbols, lv) -> dict:
    """The candidates of ``PRE-REGISTERED edges for every mode`` on paper (paper/zoo_edges.py)."""
    from . import zoo_edges as ZE
    out = {}
    sessions = [pd.Timestamp(r[0]).date() for r in rcon.execute(
        "SELECT DISTINCT business_date FROM features WHERE business_date <= ? ORDER BY 1 DESC LIMIT 30", [day]).fetchall()][::-1]
    closes = pd.DataFrame({lin: b.set_index("d")["c"] for lin, b in by.items()}).sort_index()
    # monthly: P1, P2, P3
    names = ("edge_p1_residual_mom", "edge_p2_volmanaged_mom", "edge_p3_frog_in_pan")
    st = ZE.state(z, "edge_monthly")
    if ZE._first_of_month(z, day) or not st.get("targets"):
        st = {"targets": ZE.monthly_targets(rcon, day), "set": str(day)}
        ZE.save(z, "edge_monthly", st)
    for n in names:
        if n not in done:
            out[n] = settle(z, n, day, prev, rets, ubench, st["targets"].get(n, {}))
    # every five sessions: S1
    st = ZE.state(z, "edge_s1")
    k = int(st.get("k", 0))
    if k % 5 == 0 or "w" not in st:
        st["w"] = ZE.s1_targets(rcon, day, uni, closes)
    st["k"] = k + 1
    ZE.save(z, "edge_s1", st)
    if "edge_s1_no_news_reversal" not in done:
        out["edge_s1_no_news_reversal"] = settle(z, "edge_s1_no_news_reversal", day, prev, rets, ubench, st["w"])
    # S2, daily
    if "edge_s2_reaction_drift" not in done:
        w, st2 = ZE.s2_targets(rcon, day, ZE.state(z, "edge_s2"), sessions)
        ZE.save(z, "edge_s2", st2)
        out["edge_s2_reaction_drift"] = settle(z, "edge_s2_reaction_drift", day, prev, rets, ubench, w)
    # F1 / F2, every five sessions
    if fno_symbols and "edge_f1_long_buildup" not in done:
        st = ZE.state(z, "edge_f")
        k = int(st.get("k", 0))
        if k % 5 == 0 or "f1" not in st:
            import duckdb as _dd
            fut = _dd.connect("data/futures_history.duckdb", read_only=True)
            try:
                tick = dict(rcon.execute("SELECT ticker, lineage FROM adjusted_prices WHERE business_date = ? AND ticker IN (SELECT UNNEST(?))",
                                         [day, fno_symbols]).fetchall())
                st["f1"], st["f2"] = ZE.oi_targets(fut, tick, closes, day)
            finally:
                fut.close()
        st["k"] = k + 1
        ZE.save(z, "edge_f", st)
        out["edge_f1_long_buildup"] = settle(z, "edge_f1_long_buildup", day, prev, rets, ubench, st["f1"])
        out["edge_f2_short_covering"] = settle(z, "edge_f2_short_covering", day, prev, rets, 0.0, st["f2"])
    # Z1
    if day in lv.index and "edge_z1_turn_of_month" not in done:
        e = ZE.z1_exposure(day)
        out["edge_z1_turn_of_month"] = settle(z, "edge_z1_turn_of_month", day, prev, rets, rets.get(INDEX, 0.0), {INDEX: e} if e else {})
    # batch two: W1, W2 weekly and W3 monthly in futures (market-neutral), E1 monthly
    fno_lins = [l for _, l in rcon.execute("SELECT ticker, lineage FROM adjusted_prices WHERE business_date = ? AND ticker IN (SELECT UNNEST(?))",
                                           [day, fno_symbols or []]).fetchall()]
    st = ZE.state(z, "edge_w")
    k = int(st.get("k", 0))
    if k % 5 == 0 or "W1" not in st:
        st["W1"] = ZE.ls_targets(closes, fno_lins, "W1")
        st["W2"] = ZE.ls_targets(closes, fno_lins, "W2")
    if ZE._first_of_month(z, day) or "W3" not in st:
        st["W3"] = ZE.ls_targets(closes, fno_lins, "W3")
        st["E1"] = ZE.ensemble_target(rcon, day)
        st["L2"] = ZE.l2_targets(rcon, day, fno_lins)
    st["k"] = k + 1
    ZE.save(z, "edge_w", st)
    for n, key in (("edge_w1_reversal_ls", "W1"), ("edge_w2_52wh_ls", "W2"), ("edge_w3_momentum_ls", "W3"), ("edge_l2_learned_ls", "L2")):
        if n not in done and fno_lins:
            out[n] = settle(z, n, day, prev, rets, 0.0, st.get(key, {}))
    if "edge_e1_mom_ensemble" not in done:
        out["edge_e1_mom_ensemble"] = settle(z, "edge_e1_mom_ensemble", day, prev, rets, ubench, st.get("E1", {}))
    # ADR-0004 trials: M1 every 40 sessions, F3 every 20 (both from the registration; the record never tunes them)
    if "edge_m1_momentum_book" not in done:
        w, st = ZE.m1_targets(rcon, day, ZE.state(z, "edge_m1"))
        ZE.save(z, "edge_m1", st)
        out["edge_m1_momentum_book"] = settle(z, "edge_m1_momentum_book", day, prev, rets, ubench, w)
    if fno_symbols and "edge_f3_short_buildup" not in done:
        st = ZE.state(z, "edge_f3")
        k = int(st.get("k", 0))
        if k % 20 == 0 or "w" not in st:
            import duckdb as _dd
            fut = _dd.connect("data/futures_history.duckdb", read_only=True)
            try:
                tick = dict(rcon.execute("SELECT ticker, lineage FROM adjusted_prices WHERE business_date = ? AND ticker IN (SELECT UNNEST(?))",
                                         [day, fno_symbols]).fetchall())
                st["w"] = ZE.f3_targets(fut, tick, closes, day)
            finally:
                fut.close()
        st["k"] = k + 1
        ZE.save(z, "edge_f3", st)
        out["edge_f3_short_buildup"] = settle(z, "edge_f3_short_buildup", day, prev, rets, 0.0, st["w"])
    if fno_lins and "edge_eap_announcement_premium" not in done:      # EAP, daily (batch three)
        w_eap = ZE.eap_targets(rcon, day, fno_lins)
        out["edge_eap_announcement_premium"] = settle(z, "edge_eap_announcement_premium", day, prev, rets, 0.0, w_eap)
        out["edge_eapr_announcement_short"] = settle(z, "edge_eapr_announcement_short", day, prev, rets, 0.0,
                                                     {k: -v for k, v in w_eap.items()})
    if fno_lins and "edge_bab_futures" not in done:      # BAB, monthly (trial 6, failed; on paper with its verdict)
        st = ZE.state(z, "edge_bab")
        if ZE._first_of_month(z, day) or "w" not in st:
            st = {"w": ZE.bab_targets(closes, lv["Nifty 50"], fno_lins), "set": str(day)}
            ZE.save(z, "edge_bab", st)
        out["edge_bab_futures"] = settle(z, "edge_bab_futures", day, prev, rets, 0.0, st["w"])
    if fno_lins:                                         # trial 7: the 17 chart and candle setups, daily
        try:
            for n, w in ZE.k7_targets(rcon, day, fno_lins).items():
                if n not in done:
                    out[n] = settle(z, n, day, prev, rets, 0.0, w)
        except Exception as e:                           # noqa: BLE001 - the library is heavy; the rest still settles
            print(f"k7 setups skipped tonight: {e}")
    if fno_lins and "edge_d1_delivery_ls" not in done:  # trial 8, every 20 sessions
        st = ZE.state(z, "edge_d1")
        k = int(st.get("k", 0))
        if k % 20 == 0 or "w" not in st:
            st["w"] = ZE.d1_targets(rcon, day, fno_lins)
        st["k"] = k + 1
        ZE.save(z, "edge_d1", st)
        out["edge_d1_delivery_ls"] = settle(z, "edge_d1_delivery_ls", day, prev, rets, 0.0, st["w"])
    for n, r in ZE.index_day(day).items():               # X1, X2: settled from Kite's Nifty bars
        if n not in done:
            c = INDEX_RT_I1 if r != 0.0 else 0.0
            z.execute("INSERT OR REPLACE INTO zoo_pnl VALUES (?,?,?,?,?,?,?)", [n, day, r + c, c, r, 0.0, int(r != 0.0)])
            out[n] = {"net": r}
    if "edge_i2_seasonality_ls" not in done:             # I2: settled from the day's minute bars of the 300 names
        r = ZE.i2_day(rcon, day)
        if r is not None:
            z.execute("INSERT OR REPLACE INTO zoo_pnl VALUES (?,?,?,?,?,?,?)", ["edge_i2_seasonality_ls", day, None, None, r, 0.0, 13])
            out["edge_i2_seasonality_ls"] = {"net": r}
    # I1: an intraday trade, settled from the day's minute bars
    if "edge_i1_index_intraday" not in done:
        r = ZE.i1_return(day)
        if r is not None:
            z.execute("INSERT OR REPLACE INTO zoo_pnl VALUES (?,?,?,?,?,?,?)",
                      ["edge_i1_index_intraday", day, r + INDEX_RT_I1, INDEX_RT_I1, r, 0.0, 1])
            out["edge_i1_index_intraday"] = {"net": r}
    return out


INDEX_RT_I1 = 0.0004


def summary(z) -> pd.DataFrame:
    df = z.execute("SELECT * FROM zoo_pnl ORDER BY strategy, day").df()
    if df.empty:
        return df
    g = df.groupby("strategy")
    out = pd.DataFrame({"days": g.size() - 1, "net_total": g["net"].sum(), "bench_total": g["bench"].sum(),
                        "last_net": g["net"].last(), "holdings": g["holdings"].last()})
    out["vs_bench"] = out["net_total"] - out["bench_total"]
    out["kind"] = [KIND.get(s, "book") for s in out.index]
    from ..evaluation import performance as PF
    sh = {}
    for name, x in df.groupby("strategy"):
        r = x["net"].iloc[1:]                         # the first evening only sets weights
        sh[name] = PF.stats(r, periods=252).get("sharpe") if len(r) >= 20 else float("nan")
    out["sharpe"] = pd.Series(sh)                     # annualised, above 6.5%; shown from 20 sessions
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
        if "results_reaction_short" not in done:        # Result of bad news keeps going: short rejected results, long Nifty
            from ..decisions import news_filter as NF
            bad = NF.flagged(rcon, day)
            shorts = [l for l in lins if l in bad]
            w = {l: -1.0 / len(shorts) for l in shorts}
            if shorts:
                w[INDEX] = 1.0
            out["results_reaction_short"] = settle(z, "results_reaction_short", day, prev, rets, 0.0, w)
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
    try:
        out.update(edge_strategies(rcon, z, day, prev, done, uni, by, rets, ubench, fno_symbols, lv))
    except Exception as e:                              # noqa: BLE001 - the rest of the zoo settles regardless
        print(f"edge strategies skipped tonight: {e}")
    if ALLOCATOR not in done and out:
        out[ALLOCATOR] = run_allocator(z, day, prev)
    return {"day": day, "prev": prev, "settled": len(out)}
