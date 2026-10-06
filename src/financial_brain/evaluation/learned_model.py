"""The learned-combination model as a live challenger (qualified in ``Result of the deep feature library``).

``train_final`` fits the pre-registered LightGBM once on the whole library panel (rows whose 20-session outcome ended
at least 30 days before the last date - the same embargo as the walk-forward test) and saves the model with the exact
feature list it was trained on. ``score_day`` computes the library for the most liquid names as of one close, ranks
each feature across those names (as in training) and predicts. It never retrains on live data: a refit is a new
version, pre-registered first.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

MODEL = Path("data") / "learned_model_v1.txt"
META = Path("data") / "learned_model_v1.json"
VERSION = "learned-v1"
HISTORY = 420          #: sessions of bars needed for the longest window (250) plus warm-up


class LearnedModelError(RuntimeError):
    pass


def train_final(panel: pd.DataFrame, features: list[str], *, embargo_days: int = 30,
                model_path: Path = MODEL, meta_path: Path = META) -> dict:
    import lightgbm as lgb
    from . import deep_library as DL
    p = panel.dropna(subset=["fwd20"])
    cutoff = p["d"].max() - timedelta(days=embargo_days)
    p = p[p["d"] <= cutoff]
    X = p.groupby("d")[features].rank(pct=True).to_numpy(np.float32)
    y = p.groupby("d")["fwd20"].rank(pct=True).to_numpy()
    m = lgb.LGBMRegressor(**DL.GBM_PARAMS).fit(X, y)
    m.booster_.save_model(str(model_path))
    meta = {"version": VERSION, "features": features, "trained_through": str(cutoff), "rows": int(len(p)),
            "params": DL.GBM_PARAMS}
    meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


def load(model_path: Path = MODEL, meta_path: Path = META):
    import lightgbm as lgb
    if not model_path.exists() or not meta_path.exists():
        raise LearnedModelError("no trained model - run train_final first")
    return lgb.Booster(model_file=str(model_path)), json.loads(meta_path.read_text(encoding="utf-8"))


def score_frame(frame: pd.DataFrame, booster, features: list[str]) -> pd.Series:
    """``frame``: one row per name for one day, with the features. Ranked per feature across the names, as trained."""
    X = frame[features].rank(pct=True).to_numpy(np.float32)
    return pd.Series(booster.predict(X), index=frame.index)


def score_day(rcon, day, *, names: int = 500, model_path: Path = MODEL, meta_path: Path = META) -> pd.DataFrame:
    from ..features import library as LB
    from ..features import technical as TE
    booster, meta = load(model_path, meta_path)
    lins = [r[0] for r in rcon.execute("""SELECT lineage FROM features WHERE business_date = ? AND adv20 > 0
                                          ORDER BY adv20 DESC LIMIT ?""", [day, names]).fetchall()]
    rcon.execute(TE.BARS_SQL)
    bars = rcon.execute("""SELECT lineage, d, o, h, l, c, v FROM _tbars WHERE lineage IN (SELECT UNNEST(?))
                           AND d <= ? AND d > ?::DATE - INTERVAL 700 DAY ORDER BY lineage, d""", [lins, day, day]).df()
    mk = rcon.execute("""SELECT business_date, close_level FROM index_levels_canonical WHERE index_name = 'Nifty 500'
                         AND variant = 'PRICE' AND business_date <= ?""", [day]).df()
    market = pd.Series(mk["close_level"].to_numpy(), index=pd.to_datetime(mk["business_date"]).dt.date)
    bars["d"] = pd.to_datetime(bars["d"]).dt.date
    rows = []
    for lin, g in bars.groupby("lineage", sort=False):
        if len(g) < 260:
            continue
        f = LB.compute(g.tail(HISTORY), market)
        last = f.iloc[-1]
        if last["d"] != pd.Timestamp(day).date():
            continue                                    # no bar on the day: not scored
        rows.append({"lineage": lin, **{k: last.get(k, np.nan) for k in meta["features"]}})
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise LearnedModelError(f"no names scored on {day}")
    frame["pred"] = score_frame(frame, booster, meta["features"])
    tick = dict(rcon.execute("SELECT lineage, ticker FROM adjusted_prices WHERE business_date = ? AND lineage IN (SELECT UNNEST(?))",
                             [day, list(frame["lineage"])]).fetchall())
    frame["ticker"] = frame["lineage"].map(tick)
    return frame[["lineage", "ticker", "pred"]].sort_values("pred", ascending=False).reset_index(drop=True)
