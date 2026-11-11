"""Trial 14: ``PRE-REGISTERED intraday chart candle and indicator family`` (forty candidates, thirty-five evaluated).

    python scripts/trials/v2_intraday_family.py scan        # per-symbol signals + grids -> data/edges/v2/
    python scripts/trials/v2_intraday_family.py judge       # universe, outcomes, gates, null, family tests -> result JSON
    python scripts/trials/v2_intraday_family.py record      # append the ledger row + manifest (after judge)

Reads data/minute_history.duckdb and data/edges/nifty50_minute.parquet read-only. Nothing here changes a rule after a
result: the rules live in ``financial_brain.evaluation.intraday_v2`` and were registered first.
"""
from __future__ import annotations

import json
import pickle
import sys
import time
import warnings
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

warnings.simplefilter("ignore")

OUT = Path("data/edges/v2")
CUTOFF = "2026-10-09"
TOP_N = 100
MIN_TRADES_MONTH = 10
NAME = "V2 intraday chart candle and indicator family (40 candidates)"
PREREG = "intraday chart candle and indicator family"
_CTX: dict = {}


def _init():
    import duckdb
    from financial_brain.evaluation import intraday_v2 as V
    n = pd.read_parquet("data/edges/nifty50_minute.parquet", columns=["ts", "open", "high", "low", "close", "volume"])
    n = n[n["ts"] < pd.Timestamp(CUTOFF, tz=n["ts"].dt.tz)]
    _CTX["nifty"] = V.nifty_context(n)
    rg = duckdb.connect("data/financial_brain.duckdb", read_only=True).execute(
        "SELECT business_date, regime FROM market_regime WHERE version = 'v2' ORDER BY 1").df()
    s = pd.Series(rg["regime"].to_numpy(), index=pd.to_datetime(rg["business_date"]))
    _CTX["regime"] = s.shift(1)                                    # the previous session's label
    basis = pd.read_parquet("data/basis_contracts.parquet")
    fno: dict = {}
    for sym, a, b in zip(basis["symbol"], pd.to_datetime(basis["start"]), pd.to_datetime(basis["expiry"])):
        fno.setdefault(sym, set()).update(pd.bdate_range(a, b))
    _CTX["fno"] = fno


def _scan(sym: str):
    import duckdb
    from financial_brain.evaluation import intraday_v2 as V
    path = OUT / f"{sym}.pkl"
    if path.exists():
        return sym, "cached"
    t = time.time()
    con = duckdb.connect("data/minute_history.duckdb", read_only=True)
    m = con.execute("SELECT ts, open, high, low, close, volume FROM minute_bars WHERE tradingsymbol = ? AND ts < ? "
                    "ORDER BY ts", [sym, CUTOFF]).df()
    con.close()
    if len(m) < 20000:
        return sym, "too short"
    r = V.scan_symbol(sym, m, _CTX["fno"].get(sym, set()), _CTX["nifty"], _CTX["regime"])
    r["ok"] = np.array([d in _CTX["fno"].get(sym, ()) for d in r["days"]]) if len(r["days"]) else np.array([])
    tmp = path.with_suffix(".tmp")
    with tmp.open("wb") as f:
        pickle.dump(r, f, protocol=4)
    tmp.replace(path)
    return sym, f"{len(r['signals'])} signals {round(time.time() - t)}s"


def scan(workers: int = 5):
    import duckdb
    OUT.mkdir(parents=True, exist_ok=True)
    basis = pd.read_parquet("data/basis_contracts.parquet")
    fno = set(basis["symbol"])
    con = duckdb.connect("data/minute_history.duckdb", read_only=True)
    have = {r[0] for r in con.execute("SELECT DISTINCT tradingsymbol FROM minute_bars").fetchall()}
    con.close()
    syms = sorted(have & fno)
    print("F&O names with minute history:", len(syms), "of", len(fno), flush=True)
    t0 = time.time()
    with Pool(workers, initializer=_init) as pool:
        for i, (s, msg) in enumerate(pool.imap_unordered(_scan, syms), 1):
            print(f"[{i}/{len(syms)}] {s}: {msg}  ({round(time.time() - t0)}s)", flush=True)


# ------------------------------------------------------------------------------------------------- judge

def _load():
    out = {}
    for p in sorted(OUT.glob("*.pkl")):
        with p.open("rb") as f:
            r = pickle.load(f)
        if r.get("grid") is not None:
            out[r["sym"]] = r
    return out


def eligible(scans: dict) -> dict:
    """quarter (Period) -> set of symbols: the TOP_N by median daily traded value over the previous 60 sessions, measured
    at the quarter's first session. The first quarter has no history and trades nothing."""
    V = pd.DataFrame({s: pd.Series(r["value"], index=pd.to_datetime(r["days"])) for s, r in scans.items()})
    V = V.sort_index()
    med = V.rolling(60, min_periods=40).median().shift(1)
    q = V.index.to_period("Q")
    first = pd.Series(V.index, index=V.index).groupby(q).first()
    out = {}
    for per, d in first.items():
        row = med.loc[d].dropna()
        if len(row) >= 20:
            out[per] = set(row.nlargest(TOP_N).index)
    return out


def monthly(ev: pd.DataFrame, col: str = "net") -> pd.Series:
    if ev is None or ev.empty:
        return pd.Series(dtype=float)
    d = pd.to_datetime(ev["date"])
    g = ev[col].groupby(d.dt.to_period("M"))
    s = g.mean()[g.size() >= MIN_TRADES_MONTH]
    s.index = s.index.to_timestamp()
    return s


def null_rule(rule: str, ev: pd.DataFrame, scans: dict, cand: dict, G, draws: int, seed: int) -> dict:
    """G4: each real trade is replaced by one in the same symbol on a random other session of the same quarter, same entry
    slot, direction and stop/target distances. Same exit model."""
    from financial_brain.evaluation import intraday_v2 as V
    from financial_brain.evaluation import standard as S
    rng = np.random.default_rng(seed)
    real = S.replication(monthly(ev))["stat"]
    gi = (ev["sym"].map(G["off"]).to_numpy() + ev["di"].to_numpy()).astype(int)
    key = list(zip(ev["sym"], pd.to_datetime(ev["date"]).dt.to_period("Q")))
    groups: dict = {}
    for n, k in enumerate(key):
        groups.setdefault(k, []).append(n)
    e, d = ev["e"].to_numpy(), ev["dir"].to_numpy()
    sp, tp, last = ev["stop_pct"].to_numpy(), ev["tgt_pct"].to_numpy(), ev["last"].to_numpy()
    trail = ev["trail_pct"].to_numpy()
    sims = []
    month = pd.to_datetime(ev["date"]).dt.to_period("M")
    for _ in range(draws):
        pick = gi.copy()
        for k, idx in groups.items():
            c = cand.get(k)
            if c is None or len(c) == 0:
                continue
            pick[idx] = c[rng.integers(0, len(c), size=len(idx))]
        O, H, L, C = G["o"][pick].astype(float), G["h"][pick].astype(float), G["l"][pick].astype(float), \
            G["c"][pick].astype(float)
        ep = O[np.arange(len(pick)), e]
        gross, _, _ = V.simulate(O, H, L, C, e, d, sp, tp, last, ep)
        for t in np.flatnonzero(np.isfinite(trail)):
            gross[t] = V.simulate_trail(O[t], H[t], L[t], C[t], int(e[t]), int(d[t]), sp[t], trail[t], int(last[t]), ep[t])
        net = gross - V.COST
        s = pd.Series(net).groupby(month.to_numpy()).agg(["mean", "size"])
        s = s["mean"][s["size"] >= MIN_TRADES_MONTH]
        s.index = s.index.to_timestamp()
        sims.append(S.replication(s)["stat"])
    sims = np.array([x for x in sims if np.isfinite(x)])
    q = float(np.quantile(sims, S.NULL_QUANTILE)) if len(sims) else float("nan")
    return {"stat": real, "null_q95": q, "null_mean": float(sims.mean()) if len(sims) else float("nan"),
            "draws": int(len(sims)), "pass": bool(np.isfinite(real) and np.isfinite(q) and real > q)}


def judge(draws: int = 200):
    from financial_brain.evaluation import family_validation as FV
    from financial_brain.evaluation import intraday_v2 as V
    from financial_brain.evaluation import standard as S
    t0 = time.time()
    scans = _load()
    print("symbols scanned:", len(scans), flush=True)
    elig = eligible(scans)
    frames, grids = [], {}
    for s, r in scans.items():
        sig = r["signals"]
        if not len(sig):
            grids[s] = r["grid"]
            continue
        q = pd.to_datetime(sig["date"]).dt.to_period("Q")
        sig = sig[[s in elig.get(x, ()) for x in q]]
        frames.append(sig)
        grids[s] = r["grid"]
    sig = pd.concat(frames, ignore_index=True)
    print("signals in the universe:", len(sig), f"({round(time.time() - t0)}s)", flush=True)
    ev = V.outcomes(sig, grids)
    ev = ev[np.isfinite(ev["gross"])].copy()
    ev["net20"] = ev["gross"] - 0.0020
    # concatenated grids and candidate sessions for the null
    syms = sorted(grids)
    off, rows = {}, 0
    for s in syms:
        off[s] = rows
        rows += len(grids[s]["c"])
    G = {k: np.concatenate([grids[s][k] for s in syms]) for k in "ohlc"}
    G["off"] = off
    cand: dict = {}
    for s in syms:
        r = scans[s]
        days = pd.to_datetime(r["days"])
        q = days.to_period("Q")
        ok = r["ok"]
        for per in pd.unique(q):
            idx = np.flatnonzero((q == per) & ok)
            if len(idx) > 1:
                cand[(s, per)] = off[s] + idx
    res, monthlies = {}, {}
    for rule in list(V.RULES):
        e = ev[ev["rule"] == rule]
        net_m = monthly(e)
        gross_m = monthly(e, "gross")
        n20_m = monthly(e, "net20")
        g3 = S.replication(net_m) if len(net_m) else {"pass": False, "t_a": np.nan, "t_b": np.nan, "stat": np.nan,
                                                       "mean_a": np.nan, "mean_b": np.nan, "n_a": 0, "n_b": 0}
        gg = S.replication(gross_m) if len(gross_m) else {}
        g20 = S.replication(n20_m) if len(n20_m) else {}
        row = {"trades": int(len(e)), "months": int(len(net_m)), "long_share": float((e["dir"] > 0).mean()) if len(e) else None,
               "gross_bp": [gg.get("mean_a", np.nan) * 1e4, gg.get("mean_b", np.nan) * 1e4],
               "net_bp": [g3["mean_a"] * 1e4, g3["mean_b"] * 1e4], "t": [g3["t_a"], g3["t_b"]],
               "net20_t": [g20.get("t_a", np.nan), g20.get("t_b", np.nan)],
               "win_rate": float((e["net"] > 0).mean()) if len(e) else None, "g3": g3["pass"]}
        if g3["pass"] and rule not in V.CONTROLS:
            row["g4"] = null_rule(rule, e, scans, cand, G, draws, seed=20261111)
        else:
            row["g4"] = {"pass": False, "why": "not run: G3 not passed" if rule not in V.CONTROLS else "control"}
        res[rule] = row
        monthlies[rule] = net_m
        print(f"{rule:4s} n={row['trades']:7d} mo={row['months']:3d} gross {row['gross_bp'][0]:+6.1f}/{row['gross_bp'][1]:+6.1f}"
              f" net {row['net_bp'][0]:+6.1f}/{row['net_bp'][1]:+6.1f} bp  t {g3['t_a']:+6.2f}/{g3['t_b']:+6.2f}"
              f"{'  G3 PASS' if g3['pass'] else ''}{'  G4 PASS' if row['g4'].get('pass') else ''}", flush=True)
    # family tests on rules with enough months; aligned months
    inc = [r for r in V.FAMILY if res[r]["months"] >= 100]
    X = pd.DataFrame({r: monthlies[r] for r in inc}).dropna()
    fam = {"included": inc, "excluded_thin": [r for r in V.FAMILY if r not in inc], "months_aligned": int(len(X))}
    if len(X) >= 32 and len(inc) >= 2:
        fam["reality_check"] = FV.reality_check(X, reps=2000)
        fam["pbo"] = FV.pbo(X, blocks=16 if len(X) >= 64 else 8)
    from scipy.stats import norm
    fam["bonferroni_t"] = float(norm.isf(0.05 / (2 * 35)))
    out = {}
    for rule, row in res.items():
        if rule in V.CONTROLS:
            out[rule] = {**row, "verdict": "CONTROL"}
            continue
        j = S.judge(PREREG, monthlies[rule], null=row["g4"]) if len(monthlies[rule]) else \
            {"verdict": S.FAIL, "failed": ["G3"], "gates": {}}
        out[rule] = {**row, "verdict": j["verdict"], "failed": j["failed"], "g1": j["gates"].get("G1", {}).get("status")}
    result = {"name": NAME, "family": fam, "rules": out, "untestable": V.UNTESTABLE, "universe_top_n": TOP_N,
              "symbols": len(scans), "signals": int(len(sig)), "trades": int(len(ev)), "draws": draws,
              "cost_round_trip": V.COST, "recorded_through": CUTOFF}
    Path("data/edges").mkdir(parents=True, exist_ok=True)
    Path("data/edges/v2_result.json").write_text(json.dumps(result, default=str, indent=1), encoding="utf-8")
    ev.to_parquet("data/edges/v2_events.parquet")
    print("family", {k: v for k, v in fam.items() if k not in ("included",)}, flush=True)
    print("done", round(time.time() - t0), "s", flush=True)


def record():
    import datetime as dt
    import duckdb
    from financial_brain.evaluation import standard as S
    from financial_brain.research import reproduce as RP
    r = json.loads(Path("data/edges/v2_result.json").read_text(encoding="utf-8"))
    rules = {k: {"t_a": round(v["t"][0], 2) if v["t"][0] == v["t"][0] else None,
                 "t_b": round(v["t"][1], 2) if v["t"][1] == v["t"][1] else None, "trades": v["trades"],
                 "verdict": v["verdict"]} for k, v in r["rules"].items()}
    cands = [k for k, v in r["rules"].items() if v["verdict"] == "CANDIDATE"]
    fam = r["family"]
    row = S.record({"name": NAME, "prereg": f"PRE-REGISTERED {PREREG}", "verdict": "CANDIDATE" if cands else "FAIL",
                    "failed": [] if cands else ["G3"], "rules": rules, "candidates": cands,
                    "family": {"months": fam.get("months_aligned"), "pbo": (fam.get("pbo") or {}).get("pbo"),
                               "rc_p": (fam.get("reality_check") or {}).get("rc_p"),
                               "spa_p": (fam.get("reality_check") or {}).get("spa_p"),
                               "best": (fam.get("reality_check") or {}).get("best")},
                    "untestable": list(r["untestable"]), "note": "see data/edges/v2_result.json; candidates are leads for "
                    "forward paper only (G5 not met)"})
    rcon = duckdb.connect("data/financial_brain.duckdb", read_only=True)
    man = RP.manifest(rcon, NAME)
    man["taken_at"] = dt.datetime.now().isoformat(timespec="seconds")
    Path("docs/trials/manifests/v2_intraday.json").write_text(json.dumps(man, indent=1, default=str) + "\n", encoding="utf-8")
    print(json.dumps(row, default=str)[:600])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    {"scan": scan, "judge": judge, "record": record}[cmd]()
