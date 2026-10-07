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


#: Neutral names for the features; each reason adds where the name sits on it today (high / middle / low against the
#: 500 names scored) and which way it pushed the score.
LABELS = {
    "cross_50_200": "50-day vs 200-day average", "cross_20_50": "20-day vs 50-day average", "ma_slope_200": "200-day average slope",
    "ma_slope_100": "100-day average slope", "ma_slope_50": "50-day average slope", "ma_slope_20": "20-day average slope",
    "supertrend_dir": "Supertrend direction", "vol_250": "1-year volatility", "vol_60": "60-day volatility", "vol_20": "20-day volatility",
    "vol_5": "5-day volatility", "atr_pct": "daily range", "mom_12_1": "12-month momentum", "roc_250": "1-year return",
    "roc_120": "6-month return", "roc_5": "last week's return", "roc_20": "last month's return", "dist_52w_high": "distance from the 52-week high",
    "amihud_20": "illiquidity", "turnover_20_250": "turnover vs its year", "turnover_5_20": "turnover vs its month",
    "wyckoff_compression": "Wyckoff compression", "ichi_cloud_pos": "position vs the Ichimoku cloud", "ichi_chikou": "Ichimoku lagging line",
    "fib_golden_pocket_60": "Fibonacci golden pocket (60d)", "fib_golden_pocket_250": "Fibonacci golden pocket (1y)",
    "fib_near_250": "distance from Fibonacci levels (1y)", "fib_retrace_250": "share of the 1-year swing given back",
    "fib_retrace_60": "share of the 60-day swing given back", "c_marubozu": "marubozu candle", "gap_down_unfilled": "unfilled gap down",
    "gap_open": "opening gap", "variance_ratio_10": "trendiness (variance ratio)", "max_ret_20": "largest daily jump in a month",
    "rsi_14": "RSI", "dist_ma_200": "distance from the 200-day average", "bb_width": "Bollinger band width",
    "fvg_bull_open": "open bullish fair value gaps", "fvg_bear_open": "open bearish fair value gaps", "cmf_20": "Chaikin money flow",
    "avwap_52w_high": "distance from the VWAP anchored at the 52-week high", "avwap_52w_low": "distance from the VWAP anchored at the 52-week low",
    "ob_bull_dist": "distance above a bullish order block", "dist_vwap_20": "distance from the 20-day VWAP",
    "hh_hl_count": "higher highs and higher lows", "dist_fib_ma_144": "distance from the 144-day average",
    "fib_ext1618_dist_60": "distance from the 1.618 extension (60d)", "force_index_13": "force index", "outside_bar": "outside bar",
}


def drivers(booster, ranked: np.ndarray, features: list[str], *, up: int = 5, down: int = 3) -> list[str]:
    """Each name's score split into per-feature contributions (LightGBM's own tree attribution), as plain text:
    the largest pushes up, then the largest pushes down - each with where the name sits on that feature."""
    contrib = booster.predict(ranked, pred_contrib=True)[:, :-1]
    level = lambda x: "high" if x > 0.67 else "low" if x < 0.33 else "middle"            # noqa: E731
    out = []
    for row, ranks in zip(contrib, ranked):
        order = np.argsort(row)
        name = lambda i: f"{LABELS.get(features[i], features[i].replace('_', ' '))} {level(ranks[i])}"   # noqa: E731
        pos = [f"+ {name(i)}" for i in order[::-1][:up] if row[i] > 0]
        neg = [f"- {name(i)}" for i in order[:down] if row[i] < 0]
        out.append("; ".join(pos + neg))
    return out


CACHE = Path("data") / "learned_scores"


def score_day(rcon, day, *, names: int = 500, model_path: Path = MODEL, meta_path: Path = META,
              cache: Path | None = CACHE) -> pd.DataFrame:
    """Cached per day (``data/learned_scores/DAY.parquet``), so the competition, the books and the terminal score once."""
    if cache is not None:
        f = cache / f"{pd.Timestamp(day).date()}.parquet"
        if f.exists():
            return pd.read_parquet(f)
    out = _score_day(rcon, day, names=names, model_path=model_path, meta_path=meta_path)
    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)
        out.to_parquet(cache / f"{pd.Timestamp(day).date()}.parquet")
    return out


def _score_day(rcon, day, *, names: int = 500, model_path: Path = MODEL, meta_path: Path = META) -> pd.DataFrame:
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
    frame["why"] = drivers(booster, frame[meta["features"]].rank(pct=True).to_numpy(np.float32), meta["features"])
    tick = dict(rcon.execute("SELECT lineage, ticker FROM adjusted_prices WHERE business_date = ? AND lineage IN (SELECT UNNEST(?))",
                             [day, list(frame["lineage"])]).fetchall())
    frame["ticker"] = frame["lineage"].map(tick)
    return frame[["lineage", "ticker", "pred", "why"]].sort_values("pred", ascending=False).reset_index(drop=True)
