"""The learned-combination challenger; loadable artifacts require an offline maturity manifest.

``run_offline_training_job`` hashes the versioned input, feature schema, code and seed, trains only on complete
outcomes with known maturity timestamps, and emits the manifest required by ``load``. ``train_final`` is its
low-level fitter and does not by itself create a loadable model. ``score_day`` scores without retraining.
"""
from __future__ import annotations

import json
import hashlib
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
                model_path: Path = MODEL, meta_path: Path = META, seed: int = 0) -> dict:
    """Low-level fitter. Use ``offline_training.run_offline_training_job`` for loadable artifacts."""
    if not features or len(set(features)) != len(features):
        raise LearnedModelError("feature list must be non-empty and unique")
    if embargo_days < 1 or not isinstance(seed, int):
        raise LearnedModelError("training embargo and integer seed are required")
    import lightgbm as lgb
    from . import deep_library as DL
    p = panel.dropna(subset=["fwd20"])
    cutoff = p["d"].max() - timedelta(days=embargo_days)
    p = p[p["d"] <= cutoff]
    X = p.groupby("d")[features].rank(pct=True).to_numpy(np.float32)
    y = p.groupby("d")["fwd20"].rank(pct=True).to_numpy()
    params: dict[str, Any] = {**DL.GBM_PARAMS, "random_state": seed, "n_jobs": 1,
                             "deterministic": True, "force_col_wise": True}
    m = lgb.LGBMRegressor(**params).fit(X, y)
    m.booster_.save_model(str(model_path))
    feature_source_sha256 = _file_sha256(Path(__file__).parents[1] / "features" / "library.py")
    feature_schema_sha256 = hashlib.sha256(json.dumps(
        {"features": features, "implementation_sha256": feature_source_sha256},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    data_sha256 = _training_data_sha256(p, features)
    model_sha256 = _file_sha256(model_path)
    code_sha256 = _file_sha256(Path(__file__))
    identity_params = {k: str(v) for k, v in sorted(params.items())}
    identity_params["model_artifact_sha256"] = model_sha256
    from ..forward_edge.governance import model_id
    stable_model_id = model_id(data_sha256=data_sha256, feature_schema_sha256=feature_schema_sha256,
                               params=identity_params, seed=seed, code_sha256=code_sha256)
    meta = {"version": VERSION, "model_id": stable_model_id, "features": features,
            "feature_schema_sha256": feature_schema_sha256, "training_data_sha256": data_sha256,
            "feature_source_sha256": feature_source_sha256, "model_sha256": model_sha256,
            "code_sha256": code_sha256, "seed": seed,
            "trained_through": str(cutoff), "embargo_days": embargo_days,
            "embargo_semantics": "calendar-day proxy; label-end timestamps unavailable",
            "rows": int(len(p)), "params": params, "identity_params": identity_params}
    meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


def load(model_path: Path = MODEL, meta_path: Path = META):
    import lightgbm as lgb
    if not model_path.exists() or not meta_path.exists():
        raise LearnedModelError("no trained model - run the matured offline training job first")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    required = ("model_id", "feature_schema_sha256", "training_data_sha256", "feature_source_sha256", "model_sha256")
    if any(not meta.get(key) for key in required):
        raise LearnedModelError("model predates the content-addressed lineage manifest; retrain the challenger")
    if meta.get("model_sha256") and _file_sha256(model_path) != meta["model_sha256"]:
        raise LearnedModelError("model file digest differs from the registered training manifest")
    _read_offline_training_manifest(meta, meta_path)
    return lgb.Booster(model_file=str(model_path)), meta


def _read_offline_training_manifest(meta: dict, meta_path: Path) -> dict:
    manifest_name = meta.get("offline_training_manifest_file")
    if not isinstance(manifest_name, str) or not manifest_name or Path(manifest_name).name != manifest_name:
        raise LearnedModelError("model lacks the required matured offline-training manifest")
    manifest_path = Path(meta_path).parent / manifest_name
    if not manifest_path.is_file():
        raise LearnedModelError("offline-training manifest is missing")
    try:
        training_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LearnedModelError("offline-training manifest cannot be read") from exc
    claimed_manifest_sha256 = training_manifest.get("manifest_sha256")
    manifest_body = {key: value for key, value in training_manifest.items() if key != "manifest_sha256"}
    actual_manifest_sha256 = hashlib.sha256(json.dumps(
        manifest_body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    if claimed_manifest_sha256 != actual_manifest_sha256 \
            or claimed_manifest_sha256 != meta.get("offline_training_manifest_sha256"):
        raise LearnedModelError("offline-training manifest digest does not match model metadata")
    if (training_manifest.get("model_id") != meta.get("model_id")
            or training_manifest.get("model_sha256") != meta.get("model_sha256")
            or training_manifest.get("feature_schema_sha256") != meta.get("feature_schema_sha256")
            or training_manifest.get("code_sha256") != meta.get("offline_training_code_sha256")):
        raise LearnedModelError("offline-training manifest does not match the registered model lineage")
    if (training_manifest.get("schema_version") != "fb.offline-training-job.v1"
            or training_manifest.get("maturity_contract") !=
            "label_matured_at_utc is known and <= trained_as_of_utc"
            or training_manifest.get("features") != meta.get("features")
            or training_manifest.get("seed") != meta.get("seed")
            or not training_manifest.get("dataset_version")
            or not training_manifest.get("feature_schema_version")):
        raise LearnedModelError("offline-training manifest does not satisfy the matured job contract")
    environment = training_manifest.get("environment")
    params = training_manifest.get("training_parameters")
    if (not isinstance(environment, dict)
            or any(not environment.get(key) for key in ("python", "numpy", "pandas", "lightgbm"))
            or environment.get("threads") != 1 or environment.get("deterministic") is not True
            or not isinstance(params, dict) or params.get("n_jobs") != 1
            or params.get("deterministic") is not True or params.get("force_col_wise") is not True):
        raise LearnedModelError("offline-training manifest lacks a deterministic single-thread environment")
    for field in ("dataset_sha256", "fit_dataset_sha256", "code_sha256",
                  "feature_schema_sha256", "model_sha256", "model_id"):
        digest = training_manifest.get(field)
        if not isinstance(digest, str) or len(digest) != 64 \
                or any(character not in "0123456789abcdef" for character in digest):
            raise LearnedModelError(f"offline-training manifest has invalid {field}")
    try:
        as_of = pd.Timestamp(training_manifest["trained_as_of_utc"])
        mature = pd.Timestamp(training_manifest["max_eligible_label_matured_at_utc"])
        counts_valid = (type(training_manifest["input_rows"]) is int
                        and type(training_manifest["eligible_matured_rows"]) is int
                        and type(training_manifest["quarantined_rows"]) is int
                        and training_manifest["input_rows"] >= training_manifest["eligible_matured_rows"] > 0
                        and training_manifest["quarantined_rows"] ==
                        training_manifest["input_rows"] - training_manifest["eligible_matured_rows"])
    except (KeyError, TypeError, ValueError):
        raise LearnedModelError("offline-training manifest has invalid maturity timestamps or row counts") from None
    if (as_of.tzinfo is None or mature.tzinfo is None or mature > as_of
            or not counts_valid):
        raise LearnedModelError("offline-training manifest includes invalid or immature training outcomes")
    return training_manifest


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _training_data_sha256(panel: pd.DataFrame, features: list[str]) -> str:
    """Digest the exact ordered, label-bearing training rows with stable date/float encodings."""
    required = {"d", "lineage", "fwd20", *features}
    missing = required - set(panel.columns)
    if missing:
        raise LearnedModelError(f"training data missing lineage fields: {sorted(missing)}")
    if panel.duplicated(["d", "lineage"]).any():
        raise LearnedModelError("training rows must be unique by date and lineage")
    rows = []
    for row in panel.sort_values(["d", "lineage"], kind="mergesort")[
            ["d", "lineage", *features, "fwd20"]].itertuples(index=False, name=None):
        day, lineage, *values = row
        encoded = [day.isoformat() if hasattr(day, "isoformat") else str(day), str(lineage)]
        encoded.extend(None if pd.isna(value) else float(value).hex() for value in values)
        rows.append(encoded)
    body = json.dumps(rows, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def prediction_batch_sha256(frame: pd.DataFrame, *, as_of, model_digest: str) -> str:
    """Content hash a prediction batch independent of input row order or display formatting."""
    if "lineage" not in frame or "pred" not in frame or not model_digest:
        raise LearnedModelError("prediction batch needs lineage, predictions and model identity")
    if frame["lineage"].isna().any() or frame["lineage"].duplicated().any():
        raise LearnedModelError("prediction batch lineages must be present and unique")
    rows = []
    for row in frame[["lineage", "pred"]].sort_values("lineage", kind="mergesort").itertuples(index=False):
        score = float(row.pred)
        if not np.isfinite(score):
            raise LearnedModelError("prediction batch contains a non-finite score")
        rows.append([str(row.lineage), score.hex()])
    body = json.dumps({"as_of": str(as_of), "model_id": model_digest, "predictions": rows},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _valid_prediction_cache(frame: pd.DataFrame, *, as_of, meta: dict, model_path: Path,
                            meta_path: Path | None = None) -> bool:
    """Accept only cache rows matching the current model artifact and their own content digest."""
    required = {"prediction_batch_sha256", "model_id", "feature_schema_sha256", "lineage", "pred"}
    if not required.issubset(frame.columns) or not model_path.exists():
        return False
    if (_file_sha256(model_path) != meta.get("model_sha256") or not meta.get("model_id")
            or meta_path is None):
        return False
    try:
        _read_offline_training_manifest(meta, meta_path)
    except LearnedModelError:
        return False
    if frame["model_id"].nunique() != 1 or frame["model_id"].iloc[0] != meta["model_id"]:
        return False
    if frame["feature_schema_sha256"].nunique() != 1 \
            or frame["feature_schema_sha256"].iloc[0] != meta.get("feature_schema_sha256"):
        return False
    if frame["prediction_batch_sha256"].nunique() != 1:
        return False
    return frame["prediction_batch_sha256"].iloc[0] == prediction_batch_sha256(
        frame, as_of=as_of, model_digest=meta["model_id"])


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
            cached = pd.read_parquet(f)
            metadata = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
            if _valid_prediction_cache(cached, as_of=pd.Timestamp(day).date(), meta=metadata,
                                       model_path=model_path, meta_path=meta_path):
                return cached
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
    batch_hash = prediction_batch_sha256(frame.assign(lineage=frame["lineage"], pred=frame["pred"]),
                                         as_of=pd.Timestamp(day).date(), model_digest=meta["model_id"])
    frame["prediction_batch_sha256"] = batch_hash
    frame["model_id"] = meta["model_id"]
    frame["feature_schema_sha256"] = meta["feature_schema_sha256"]
    frame["why"] = drivers(booster, frame[meta["features"]].rank(pct=True).to_numpy(np.float32), meta["features"])
    tick = dict(rcon.execute("SELECT lineage, ticker FROM adjusted_prices WHERE business_date = ? AND lineage IN (SELECT UNNEST(?))",
                             [day, list(frame["lineage"])]).fetchall())
    frame["ticker"] = frame["lineage"].map(tick)
    cols = ["lineage", "ticker", "pred", "why"] + [f for f in meta["features"] if f in frame.columns]
    return frame[cols].sort_values("pred", ascending=False).reset_index(drop=True)   # features kept for percentiles
