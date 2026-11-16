"""Defined-risk NIFTY premium selling on observed NSE closes - TRAINING RANGE ONLY (2016-2021).

The 2022+ range is the project's sealed options holdout (``evaluation/options_real.py``); this script refuses to
read it. Three structures, fixed here before the run, each entered 15 sessions before the monthly expiry at the
close and held to expiry:

    IC   iron condor       short 4% OTM put and call, long 7% OTM wings
    PCS  put credit spread short 4% OTM put, long 7% OTM put
    IB   iron butterfly    short the at-the-money straddle, long 4% OTM wings

Entry prices are daily closes (not bid/ask): every leg pays a 2% haircut plus one index point, and an in-the-money
long leg pays 0.125% exercise STT on its intrinsic value. The return is P&L over the structure's maximum loss
(the capital at risk; SPAN margin is close to it for a defined-risk spread). Lot sizes, margin changes and early
exit are not modelled. A result here can be at most INCUBATE: CANDIDATE needs the holdout, which only the owner
may open, once, for a pre-registered spec.

    python scripts/research/options_defined_risk.py   -> docs/trials/dynamic/options-defined-risk-train.json
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from financial_brain.evaluation import dynamic_standard as D
from financial_brain.evaluation import standard as S

TRAIN_START, TRAIN_END = "2016-01-01", "2021-12-31"
ENTRY_SESSIONS = 15
HAIRCUT, FLAT = 0.02, 1.0
EXERCISE_STT = 0.00125
MIN_CONTRACTS = 100
OUT = Path("docs/trials/dynamic")
STRUCTURES = {"IC": [("PE", 0.96, -1), ("PE", 0.93, +1), ("CE", 1.04, -1), ("CE", 1.07, +1)],
              "PCS": [("PE", 0.96, -1), ("PE", 0.93, +1)],
              "IB": [("PE", 1.00, -1), ("CE", 1.00, -1), ("PE", 0.96, +1), ("CE", 1.04, +1)]}


def load(con):
    if TRAIN_END > "2021-12-31":
        raise ValueError("only the sealed 2016-2021 training range may be explored")
    fut = con.execute("""SELECT trade_date d, expiry, settle FROM fo_bhav WHERE symbol='NIFTY' AND instrument='FUTIDX'
                         AND trade_date BETWEEN ? AND ? AND expiry <= ?""", [TRAIN_START, TRAIN_END, TRAIN_END]).df()
    opt = con.execute("""SELECT trade_date d, expiry, strike, option_type typ, close, contracts FROM fo_bhav
                         WHERE symbol='NIFTY' AND instrument='OPTIDX' AND trade_date BETWEEN ? AND ? AND expiry <= ?
                         AND close > 0""", [TRAIN_START, TRAIN_END, TRAIN_END]).df()
    for df in (fut, opt):
        df["d"], df["expiry"] = pd.to_datetime(df["d"]), pd.to_datetime(df["expiry"])
    return fut, opt


def trades(fut: pd.DataFrame, opt: pd.DataFrame) -> pd.DataFrame:
    sessions = np.sort(fut["d"].unique())
    rows = []
    for exp in sorted(fut["expiry"].unique()):                       # futures expiries are the monthly ones
        f = fut[fut["expiry"] == exp].set_index("d")["settle"]
        if exp not in f.index:
            continue                                                 # expiry moved by a holiday: skipped, counted
        i = np.searchsorted(sessions, np.datetime64(exp))
        if i - ENTRY_SESSIONS < 0:
            continue
        d0 = pd.Timestamp(sessions[i - ENTRY_SESSIONS])
        if d0 not in f.index:
            continue
        fwd, final = float(f[d0]), float(f[exp])
        chain = opt[(opt["d"] == d0) & (opt["expiry"] == exp) & (opt["contracts"] >= MIN_CONTRACTS)]
        for name, legs in STRUCTURES.items():
            credit, cost, payoff, strikes, ok = 0.0, 0.0, 0.0, [], True
            for typ, m, side in legs:
                c = chain[chain["typ"] == typ]
                if c.empty:
                    ok = False
                    break
                k = c.iloc[(c["strike"] - fwd * m).abs().argmin()]
                if abs(k["strike"] / fwd - m) > 0.012:
                    ok = False
                    break
                px = float(k["close"])
                credit += -side * px
                cost += HAIRCUT * px + FLAT
                intrinsic = max(final - k["strike"], 0.0) if typ == "CE" else max(k["strike"] - final, 0.0)
                payoff += side * intrinsic
                if side > 0 and intrinsic > 0:
                    cost += EXERCISE_STT * intrinsic
                strikes.append((typ, float(k["strike"]), side))
            if not ok:
                rows.append({"expiry": exp, "structure": name, "ok": False})
                continue
            puts = sorted(s for t, s, _ in strikes if t == "PE")
            calls = sorted(s for t, s, _ in strikes if t == "CE")
            width = max(puts[-1] - puts[0] if len(puts) == 2 else 0.0, calls[-1] - calls[0] if len(calls) == 2 else 0.0)
            max_loss = width - credit + cost
            pnl = credit + payoff - cost
            rows.append({"expiry": exp, "structure": name, "ok": True, "entry": d0, "fwd": fwd, "final": final,
                         "credit": credit, "cost": cost, "pnl": pnl, "max_loss": max_loss,
                         "ret": pnl / max_loss if max_loss > 0 else np.nan, "move": final / fwd - 1})
    return pd.DataFrame(rows)


def main() -> None:
    con = duckdb.connect("data/fo_history.duckdb", read_only=True)
    t = trades(*load(con))
    res = {"label": "TRAINING ONLY 2016-2021; holdout not read; closes not quotes", "family": 8,
           "coverage": t.groupby("structure")["ok"].agg(["sum", "count"]).astype(int).to_dict("index"), "structures": {}}
    for name in STRUCTURES:
        x = t[(t["structure"] == name) & t["ok"]].set_index("expiry")
        s = x["ret"].dropna()
        g = D.judge(D.Spec(f"{name} defined-risk NIFTY premium (train)", "B", 8), s, null_pass=None,
                    split=pd.Timestamp("2019-01-01"))
        if g["tier"] in ("CANDIDATE", "CANDIDATE_SMALL"):
            g["tier"], g["why"] = "INCUBATE", g["why"] + ["training range only: the holdout is sealed"]
        g["capital_fraction"] = 0.0
        a, b = S.halves(s, pd.Timestamp("2019-01-01"))
        res["structures"][name] = {
            "months": len(s), "mean_ret_on_risk": float(s.mean()), "t": S.tstat(s), "t_2016_18": S.tstat(a),
            "t_2019_21": S.tstat(b), "win_rate": float((s > 0).mean()), "worst": float(s.min()),
            "mean_credit_pts": float(x["credit"].mean()), "mean_cost_pts": float(x["cost"].mean()),
            "mean_max_loss_pts": float(x["max_loss"].mean()), "sum_pnl_pts": float(x["pnl"].sum()),
            "worst_month": str(s.idxmin().date()), "dynamic": g}
        print(name, {k: (round(v, 4) if isinstance(v, float) else v) for k, v in res["structures"][name].items()
                     if k != "dynamic"})
        print("    ", {k: g[k] for k in ("t_used", "z_adj", "need_small", "posterior", "tier", "sharpe", "why")})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "options-defined-risk-train.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    t.to_parquet(OUT / "options-defined-risk-train-trades.parquet")


if __name__ == "__main__":
    main()
