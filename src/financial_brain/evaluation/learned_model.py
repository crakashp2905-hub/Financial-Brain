"""The learned-combination model as a live challenger (qualified in ``Result of the deep feature library``).

``train_final`` fits the pre-registered LightGBM once on the whole library panel (rows whose 20-session outcome ended
at least 30 days before the last date - the same embargo as the walk-forward test) and saves the model with the exact
feature list it was trained on. ``score_day`` computes the library for the most liquid names as of one close, ranks
each feature across those names (as in training) and predicts. It never retrains on live data: a refit is a new
version, pre-registered first.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

MODEL = Path("data") / "learned_model_v1.txt"
META = Path("data") / "learned_model_v1.json"
VERSION = "learned-v1"
HISTORY = 420          #: sessions of bars needed for the longest window (250) plus warm-up


class LearnedModelError(RuntimeError):
    pass


def train_final(panel: pd.DataFrame, features: list[str], *, embargo_days: int = 30,
                model_path: Path = MODEL, meta_path: Path = META,
                version: str = VERSION) -> dict:
    import lightgbm as lgb
    from . import deep_library as DL
    if not version.strip() or embargo_days < 0:
        raise LearnedModelError("training version and non-negative embargo are required")
    if not features or len(features) != len(set(features)):
        raise LearnedModelError("feature names must be non-empty and unique")
    missing = set(["d", "fwd20", *features]) - set(panel.columns)
    if missing:
        raise LearnedModelError(f"training panel is missing columns: {sorted(missing)}")
    if any(not pd.api.types.is_numeric_dtype(panel[name]) for name in features):
        raise LearnedModelError("training features must be numeric")
    model_path, meta_path = Path(model_path), Path(meta_path)
    if model_path.exists() or meta_path.exists():
        raise LearnedModelError("model version artifacts are immutable; choose new output paths and version")
    p = panel.dropna(subset=["fwd20"])
    if p.empty or not np.isfinite(p["fwd20"].to_numpy(dtype=float)).all():
        raise LearnedModelError("training outcomes must contain finite matured labels")
    cutoff = p["d"].max() - timedelta(days=embargo_days)
    p = p[p["d"] <= cutoff]
    if p.empty:
        raise LearnedModelError("embargo leaves no training rows")
    sort_columns = ["d", *features, "fwd20"] + (["lineage"] if "lineage" in p.columns else [])
    p = p.sort_values(sort_columns, kind="mergesort", na_position="last").reset_index(drop=True)
    if np.isinf(p[features].to_numpy(dtype=float)).any():
        raise LearnedModelError("training features cannot contain infinite values")
    x_ranked = p.groupby("d", sort=True)[features].rank(pct=True, method="average")
    y_ranked = p.groupby("d", sort=True)["fwd20"].rank(pct=True, method="average")
    X = x_ranked.to_numpy(np.float32)
    y = y_ranked.to_numpy(np.float64)
    training_data_sha256 = hashlib.sha256(
        p[["d", *features, "fwd20"]].to_json(orient="split", index=False,
                                                 date_format="iso", double_precision=15).encode("utf-8")
    ).hexdigest()
    schema = {"features": [{"name": name, "dtype": str(p[name].dtype)} for name in features],
              "target": {"name": "fwd20", "dtype": str(p["fwd20"].dtype)},
              "transform": "cross_sectional_average_percentile_rank_by_decision_date",
              "embargo_days": embargo_days}
    feature_schema_sha256 = hashlib.sha256(json.dumps(
        schema, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    params: dict[str, object] = {**DL.GBM_PARAMS, "random_state": 0, "bagging_seed": 0,
                                 "feature_fraction_seed": 0, "data_random_seed": 0,
                                 "deterministic": True, "force_col_wise": True,
                                 "num_threads": 1}
    estimator: Any = lgb.LGBMRegressor
    model = estimator(**params).fit(X, y)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    model_fd, model_tmp = tempfile.mkstemp(prefix=model_path.name + ".", dir=model_path.parent)
    meta_fd, meta_tmp = tempfile.mkstemp(prefix=meta_path.name + ".", dir=meta_path.parent)
    os.close(model_fd)
    os.close(meta_fd)
    try:
        model.booster_.save_model(model_tmp)
        model_sha256 = hashlib.sha256(Path(model_tmp).read_bytes()).hexdigest()
        meta = {"version": version, "features": features, "trained_through": str(cutoff),
                "rows": int(len(p)), "params": params, "schema": schema,
                "feature_schema_sha256": feature_schema_sha256,
                "training_data_sha256": training_data_sha256, "model_sha256": model_sha256,
                "lightgbm_version": lgb.__version__, "pandas_version": pd.__version__,
                "numpy_version": np.__version__, "seed": 0}
        Path(meta_tmp).write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(model_tmp, model_path)
        os.replace(meta_tmp, meta_path)
    except Exception:
        Path(model_tmp).unlink(missing_ok=True)
        Path(meta_tmp).unlink(missing_ok=True)
        raise
    return meta


def load(model_path: Path = MODEL, meta_path: Path = META):
    import lightgbm as lgb
    if not model_path.exists() or not meta_path.exists():
        raise LearnedModelError("no trained model - run train_final first")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("model_sha256"):
        digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
        if digest != meta["model_sha256"]:
            raise LearnedModelError("model artifact hash differs from frozen metadata")
    return lgb.Booster(model_file=str(model_path)), meta


def score_frame(frame: pd.DataFrame, booster, features: list[str]) -> pd.Series:
    """``frame``: one row per name for one day, with the features. Ranked per feature across the names, as trained."""
    X = frame[features].rank(pct=True).to_numpy(np.float32)
    return pd.Series(booster.predict(X), index=frame.index)


def prediction_lineage(frame: pd.DataFrame, meta: dict, predictions: pd.Series) -> dict[str, str]:
    """Hash the exact model, frozen schema, ordered feature inputs and prediction output."""
    features = meta.get("features")
    if not isinstance(features, list) or not features or any(name not in frame for name in features):
        raise LearnedModelError("prediction frame does not match the frozen feature schema")
    if len(predictions) != len(frame) or not np.isfinite(predictions.to_numpy(dtype=float)).all():
        raise LearnedModelError("prediction output length or values are invalid")
    if not meta.get("model_sha256") or not meta.get("feature_schema_sha256"):
        raise LearnedModelError("model metadata lacks frozen artifact/schema hashes")
    input_frame = frame[features]
    input_sha256 = hashlib.sha256(input_frame.to_json(
        orient="split", index=False, double_precision=15).encode("utf-8")).hexdigest()
    payload = {"model_sha256": meta["model_sha256"],
               "feature_schema_sha256": meta["feature_schema_sha256"],
               "prediction_input_sha256": input_sha256,
               "predictions": predictions.to_list()}
    return {**{key: str(value) for key, value in payload.items()},
            "lineage_sha256": hashlib.sha256(json.dumps(
                payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()}


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
        name = lambda i: f"{LABELS.get(features[i], features[i].replace('_', ' '))} {level(ranks[i])}"  # noqa: B023, E731
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
    cols = ["lineage", "ticker", "pred", "why"] + [f for f in meta["features"] if f in frame.columns]
    return frame[cols].sort_values("pred", ascending=False).reset_index(drop=True)   # features kept for percentiles
