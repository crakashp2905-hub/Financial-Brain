"""The loop audits itself: how often does it confirm a pattern that is not there, and how fast does it find one that is?

A learning loop that cannot state its own error rate is a story generator. This runs the *same* protocol code the nightly job
runs (``protocol.step``) over simulated histories and counts:

* **false confirmations** - histories in which a lesson was CONFIRMED although every outcome was generated with no relation to
  any context (the null: the real ledger's contexts and trade counts, its outcomes demeaned, shuffled within each day and
  sign-flipped by day, so neither trade-level nor day-level structure can carry a signal);
* **power** - with a loss of ``effect`` bp planted in one named cell, how often it is nominated and confirmed, and after how
  many sessions.

Histories are built from the real ledger by resampling its days (a bootstrap), so cell sizes and the number of trades a day are
realistic; ``synthetic_ledger`` stands in when there is no ledger yet (and in tests). The thresholds in ``protocol.Params`` were
set from this null, which carries no effects and so cannot overfit one.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import features as F
from . import protocol as P
from .store import fold


def synthetic_ledger(n_sessions: int = 60, seed: int = 0, *, trades_per_day: tuple[int, int] = (8, 14), plant: tuple | None = None,
                     start: str = "2027-01-04", sd_bp: float = 35.0) -> pd.DataFrame:
    """A labelled ledger of closed trades with no structure in the outcomes, optionally with ``plant=(dimension, value, bp)``:
    a loss of ``bp`` on trades in that cell. Used by the tests and when the real ledger is too short to resample."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start, periods=n_sessions).strftime("%Y-%m-%d")
    rows = []
    for day in days:
        mkt = rng.choice([-0.004, 0.004]) * rng.uniform(0.5, 1.5)
        up = bool(rng.integers(0, 2))
        regime = rng.choice(["RISK_ON", "NEUTRAL", "RISK_OFF"])
        vix = float(rng.choice([12.0, 16.0, 22.0]))
        for i in range(int(rng.integers(trades_per_day[0], trades_per_day[1] + 1))):
            side = "long" if rng.random() < 0.5 else "short"
            px = float(rng.uniform(100, 2000))
            rows.append({"trade_id": f"{day}-{i}", "mode": "live", "day": day, "strategy": "ORB" if rng.random() < 0.85 else "GAP",
                         "symbol": f"S{int(rng.integers(0, 120))}", "side": side, "qty": 100, "entry_px": px,
                         "entry_decision_px": px, "entry_vwap": px * (1 + rng.normal(0, 0.004)),
                         "net_pnl": 0.0, "mkt_ret": mkt + rng.normal(0, 0.002), "daily_up": up,
                         "gap": rng.normal(0, 0.01), "or_width": abs(rng.normal(0.013, 0.005)),
                         "entry_minute": int(rng.choice([571, 572, 575, 580, 600, 640], p=[.3, .2, .2, .1, .1, .1])),
                         "spread_bp": abs(rng.normal(4, 2)), "bucket": rng.choice(["mega", "large", "mid"], p=[.1, .8, .1]),
                         "regime_prev": regime, "vix": vix})
    df = pd.DataFrame(rows)
    df["daily_up"] = df["daily_up"].astype("boolean")
    notional = df["entry_px"] * df["qty"]
    bp = rng.normal(0, sd_bp, len(df))
    df["net_pnl"] = bp / 1e4 * notional
    df = F.label(df)
    if plant:
        dim, value, effect = plant
        df = plant_effect(df, dim, value, effect)
    return df


def plant_effect(df: pd.DataFrame, dimension: str, value: str, bp: float, strategy: str | None = None) -> pd.DataFrame:
    """Subtract ``bp`` from the net of every trade in the cell (a loss), keeping the labels."""
    d = df.copy()
    m = d[dimension] == value
    if strategy:
        m &= d["strategy"] == strategy
    d.loc[m, "net_pnl"] = d.loc[m, "net_pnl"] - bp / 1e4 * d.loc[m, "notional"]
    d["net_bp"] = d["net_pnl"] / d["notional"] * 1e4
    return d


def resample_history(trades: pd.DataFrame, n_sessions: int, rng: np.random.Generator, start: str = "2027-01-04") -> pd.DataFrame:
    """``n_sessions`` sessions built by drawing the ledger's days with replacement, re-dated in order."""
    days = np.array(sorted(trades["day"].unique()))
    pick = rng.choice(days, size=n_sessions, replace=True)
    dates = pd.bdate_range(start, periods=n_sessions).strftime("%Y-%m-%d")
    by_day = {d: g for d, g in trades.groupby("day")}
    parts = []
    for new, old in zip(dates, pick, strict=True):
        g = by_day[old].copy()
        g["day"] = new
        g["trade_id"] = g["trade_id"].astype(str) + "@" + new
        parts.append(g)
    return pd.concat(parts, ignore_index=True)


def null_outcomes(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Outcomes with no relation to any context: demeaned, shuffled within each day, each day's sign flipped at random."""
    d = df.copy()
    y = (d["net_bp"] - d["net_bp"].mean()).to_numpy()
    out = np.empty_like(y)
    for _day, idx in d.groupby("day").indices.items():
        v = rng.permutation(y[idx]) * rng.choice([-1.0, 1.0])
        out[idx] = v
    d["net_bp"] = out
    d["net_pnl"] = out / 1e4 * d["notional"]
    return d


def run_history(df: pd.DataFrame, params: P.Params = P.DEFAULT, *, plant_cell: str | None = None) -> dict:
    """Run the protocol night by night over a history. Returns counts and, for ``plant_cell``, when it was found."""
    sessions = sorted(df["day"].unique())
    cm = P.CellMatrix(df, sessions)
    events: list[dict] = []
    lessons: dict[str, dict] = {}
    found: dict[str, int] = {}
    for i, session in enumerate(sessions):
        if i + 1 < params.min_days:
            continue
        new = P.step(cm, df, session, lessons, params)
        events += new
        lessons = fold({"lesson_id": e["lesson_id"], "kind": e["kind"], "session": e["session"], "payload": e["payload"]}
                       for e in events)
        for e in new:
            if e["kind"] in ("NOMINATED", "CONFIRMED"):
                found.setdefault(f"{e['kind']}:{lessons[e['lesson_id']].get('cell')}", i + 1)
    kinds = [e["kind"] for e in events]
    cells = {lid: x.get("cell") for lid, x in lessons.items()}
    out = {"nominated": kinds.count("NOMINATED"), "confirmed": kinds.count("CONFIRMED"), "refuted": kinds.count("REFUTED"),
           "expired": kinds.count("EXPIRED"), "confirmed_cells": [cells[e["lesson_id"]] for e in events if e["kind"] == "CONFIRMED"]}
    if plant_cell:
        out["planted_nominated_at"] = found.get(f"NOMINATED:{plant_cell}")
        out["planted_confirmed_at"] = found.get(f"CONFIRMED:{plant_cell}")
    return out


def calibrate(trades: pd.DataFrame, *, draws: int = 100, n_sessions: int = 80, effects: tuple[float, ...] = (20.0, 40.0, 60.0),
              plant: tuple[str, str, str] = ("ORB", "vs_market_today", "against the market"), seed: int = 20261112,
              params: P.Params = P.DEFAULT, progress=None) -> dict:
    """False-confirmation rate under the null and power at each planted effect, on histories built from ``trades``."""
    rng = np.random.default_rng(seed)
    strat, dim, value = plant
    cell = F.cell_id(strat, dim, value)
    null_runs, power_runs = [], {e: [] for e in effects}
    for k in range(draws):
        hist = null_outcomes(resample_history(trades, n_sessions, rng), rng)
        null_runs.append(run_history(hist, params))
        for e in effects:
            power_runs[e].append(run_history(plant_effect(hist, dim, value, e, strat), params, plant_cell=cell))
        if progress:
            progress(k + 1, draws)
    n = len(null_runs)
    out = {"draws": n, "n_sessions": n_sessions, "params": params.__dict__,
           "null": {"histories_with_a_nomination": sum(r["nominated"] > 0 for r in null_runs) / n,
                    "nominations_per_history": float(np.mean([r["nominated"] for r in null_runs])),
                    "false_confirmation_rate": sum(r["confirmed"] > 0 for r in null_runs) / n,
                    "confirmations_per_history": float(np.mean([r["confirmed"] for r in null_runs]))},
           "power": {}}
    for e, runs in power_runs.items():
        conf = [r["planted_confirmed_at"] for r in runs if r.get("planted_confirmed_at")]
        out["power"][e] = {"effect_bp": e, "nominated": sum(bool(r.get("planted_nominated_at")) for r in runs) / n,
                           "confirmed": len(conf) / n, "median_session_confirmed": float(np.median(conf)) if conf else None}
    return out


def render(r: dict) -> str:
    nl = r["null"]
    lines = [f"Calibration on {r['draws']} simulated histories of {r['n_sessions']} sessions (the ledger's own contexts and sizes).",
             f"  with NO real effect anywhere: a nomination in {nl['histories_with_a_nomination']:.0%} of histories "
             f"({nl['nominations_per_history']:.2f} per history); a false CONFIRMATION in {nl['false_confirmation_rate']:.1%}.",
             "  with a loss planted in one cell (ORB trades against the market):"]
    for e, p in sorted(r["power"].items()):
        when = f", median session {p['median_session_confirmed']:.0f}" if p["median_session_confirmed"] else ""
        lines.append(f"    -{e:.0f} bp a trade: nominated {p['nominated']:.0%}, confirmed {p['confirmed']:.0%}{when}")
    return "\n".join(lines)
