"""Causal inverse-policy research. Action agreement is not trading alpha.

The standalone CLI is intentionally research-only and never opens a broker session.
All splits are chronological; the final holdout is evaluated once after model selection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, balanced_accuracy_score,
                             f1_score, log_loss, precision_recall_fscore_support)
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier, _tree

ACTIONS = ("BUY", "SELL", "HOLD", "INCREASE", "REDUCE", "EXIT")
FEATURES = ("momentum", "reversal", "ma_gap", "volatility", "breakout", "risk_gap",
            "trend", "age", "drawdown", "regime_bull")
POLICIES = ("momentum", "mean_reversion", "ma_crossover", "volatility_target",
            "breakout", "risk_parity", "trend_following", "time_exit", "trailing_stop")


def _utc(series: pd.Series) -> pd.Series:
    if series.isna().any():
        raise ValueError("timestamps must not be null")
    if not series.map(lambda value: pd.Timestamp(value).tzinfo is not None).all():
        raise ValueError("timestamps require an explicit timezone")
    return pd.to_datetime(series, utc=True, errors="raise")


def validate_history(frame: pd.DataFrame, *, authorized: bool = False) -> pd.DataFrame:
    """Validate one authorized decision history and reject post-decision features.

    HOLD rows must be recorded observations; missing orders are never inferred as HOLD.
    Any feature not in the explicit point-in-time schema is excluded from learning.
    """
    required = {"instrument", "decision_ts", "state_asof", "action", "quantity", "provenance", *FEATURES}
    missing = required - set(frame)
    if missing:
        raise ValueError(f"missing fields: {sorted(missing)}")
    if not authorized:
        raise PermissionError("explicit authorization is required for history import")
    out = frame.copy()
    for col in ("decision_ts", "state_asof"):
        out[col] = _utc(out[col])
    if "execution_ts" in out:
        out["execution_ts"] = _utc(out["execution_ts"])
        if (out.execution_ts < out.decision_ts).any():
            raise ValueError("execution precedes decision")
    if (out.state_asof > out.decision_ts).any():
        raise ValueError("future market state in decision history")
    if not out.action.isin(ACTIONS).all():
        raise ValueError("unknown action")
    if out[["instrument", "provenance"]].isna().any().any() or (out.quantity < 0).any():
        raise ValueError("identity, provenance, and nonnegative quantity are required")
    if not np.isfinite(out[list(FEATURES)].to_numpy(dtype=float)).all():
        raise ValueError("features must be finite")
    return out.sort_values(["decision_ts", "instrument"], kind="stable").reset_index(drop=True)


def import_history(path: str | Path, *, authorized: bool = False) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(path)
    elif path.suffix.lower() == ".parquet":
        frame = pd.read_parquet(path)
    else:
        raise ValueError("history must be CSV or Parquet")
    return validate_history(frame, authorized=authorized)


def from_existing_decisions(con, states: pd.DataFrame) -> pd.DataFrame:
    """Join recorded decisions to independently supplied as-of feature snapshots.

    The existing decisions table does not persist the complete feature vector. Missing
    snapshots are dropped and reported by the caller; we never reconstruct them from
    today's market data or label absent orders as HOLD.
    """
    decisions = con.execute("SELECT decision_id, isin, action, created_at FROM decisions ORDER BY created_at").df()
    if decisions.empty:
        raise ValueError("no recorded decisions")
    required = {"decision_id", "state_asof", *FEATURES}
    if not required <= set(states):
        raise ValueError(f"state snapshots missing {sorted(required - set(states))}")
    if states.decision_id.duplicated().any():
        raise ValueError("ambiguous state snapshot for decision")
    joined = decisions.merge(states, on="decision_id", how="inner", validate="one_to_one")
    if joined.empty:
        raise ValueError("no decisions have recorded as-of state snapshots")
    joined = joined.rename(columns={"isin": "instrument", "created_at": "decision_ts"})
    joined["quantity"] = joined.get("quantity", 0.0)
    joined["provenance"] = joined.get("provenance", "financial_brain:decisions+state_snapshot")
    return validate_history(joined, authorized=True)


def synthetic_history(policy: str, *, n: int = 900, seed: int = 7) -> pd.DataFrame:
    """Known-rule fixture, not a market backtest or evidence of profitable alpha."""
    if policy not in POLICIES or n < 90:
        raise ValueError("unknown policy or fewer than 90 observations")
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n, len(FEATURES)))
    x[:, 3] = np.abs(x[:, 3]) + 0.1
    x[:, 7] = rng.integers(1, 90, size=n)
    x[:, 8] = -np.abs(x[:, 8])
    x[:, 9] = rng.integers(0, 2, size=n)
    f = pd.DataFrame(x, columns=FEATURES)
    actions = np.full(n, "HOLD", dtype=object)
    if policy == "momentum":
        actions = np.where(f.momentum > 0.35, "BUY", np.where(f.momentum < -0.35, "SELL", "HOLD"))
    elif policy == "mean_reversion":
        actions = np.where(f.reversal < -0.45, "BUY", np.where(f.reversal > 0.45, "SELL", "HOLD"))
    elif policy == "ma_crossover":
        actions = np.where(f.ma_gap > 0.25, "BUY", np.where(f.ma_gap < -0.25, "SELL", "HOLD"))
    elif policy == "volatility_target":
        actions = np.where(f.volatility < 0.7, "INCREASE", np.where(f.volatility > 1.6, "REDUCE", "HOLD"))
    elif policy == "breakout":
        actions = np.where(f.breakout > 0.5, "BUY", np.where(f.breakout < -0.5, "EXIT", "HOLD"))
    elif policy == "risk_parity":
        actions = np.where(f.risk_gap > 0.3, "REDUCE", np.where(f.risk_gap < -0.3, "INCREASE", "HOLD"))
    elif policy == "trend_following":
        actions = np.where((f.trend > 0.2) & (f.regime_bull == 1), "BUY",
                           np.where(f.trend < -0.2, "SELL", "HOLD"))
    elif policy == "time_exit":
        actions = np.where(f.age >= 45, "EXIT", "HOLD")
    elif policy == "trailing_stop":
        actions = np.where(f.drawdown < -1.0, "EXIT", "HOLD")
    f.insert(0, "instrument", "SYNTHETIC")
    f.insert(1, "decision_ts", pd.date_range("2016-01-01", periods=n, freq="D", tz="UTC"))
    f.insert(2, "state_asof", f.decision_ts - pd.Timedelta(minutes=1))
    f["action"] = actions
    f["quantity"] = 1.0
    f["provenance"] = f"synthetic:{policy}:seed={seed}"
    f["generating_policy"] = policy
    return validate_history(f, authorized=True)


def _model(name: str, seed: int):
    if name == "logistic":
        return make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, random_state=seed))
    if name == "tree":
        return DecisionTreeClassifier(max_depth=5, min_samples_leaf=12, random_state=seed)
    if name == "forest":
        return RandomForestClassifier(n_estimators=100, max_depth=8, min_samples_leaf=8,
                                      n_jobs=1, random_state=seed)
    if name == "boosted":
        return HistGradientBoostingClassifier(max_iter=80, max_leaf_nodes=12, random_state=seed)
    if name == "neural":
        return make_pipeline(StandardScaler(), MLPClassifier(hidden_layer_sizes=(24,), alpha=0.01,
                             early_stopping=False, max_iter=150, random_state=seed))
    raise ValueError(f"unknown model: {name}")


def _metrics(y, probabilities, classes) -> dict:
    if not set(y) <= set(classes):
        raise ValueError("evaluation contains actions unseen in training")
    pred = np.asarray(classes)[np.argmax(probabilities, axis=1)]
    labels = sorted(set(y) | set(classes))
    p, r, _, s = precision_recall_fscore_support(y, pred, labels=labels, zero_division=0)
    return {"n": len(y), "accuracy": float(accuracy_score(y, pred)),
            "macro_f1": float(f1_score(y, pred, average="macro", zero_division=0)),
            "balanced_accuracy": float(balanced_accuracy_score(y, pred)),
            "nll": float(log_loss(y, probabilities, labels=classes)),
            "multiclass_brier": float(np.mean(np.sum((probabilities -
                (np.asarray(y)[:, None] == np.asarray(classes)[None, :])) ** 2, axis=1))),
            "by_class": {label: {"precision": float(a), "recall": float(b), "support": int(c)}
                         for label, a, b, c in zip(labels, p, r, s)}}


@dataclass
class InferenceResult:
    model_name: str
    model: object
    classes: list[str]
    validation: dict
    holdout: dict
    split: dict
    seed: int
    majority_baseline: dict | None = None


def _chronological_parts(data: pd.DataFrame, embargo: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split on unique timestamps so simultaneous instruments cannot cross folds."""
    timestamps = data.decision_ts.drop_duplicates().sort_values().reset_index(drop=True)
    if len(timestamps) < 100:
        raise ValueError("at least 100 distinct decision times required")
    a, b = int(len(timestamps) * .6), int(len(timestamps) * .8)
    if a + embargo >= b or b + embargo >= len(timestamps):
        raise ValueError("embargo leaves no evaluation period")
    train = data.loc[data.decision_ts < timestamps.iloc[a]]
    val = data.loc[(data.decision_ts >= timestamps.iloc[a + embargo])
                   & (data.decision_ts < timestamps.iloc[b])]
    test = data.loc[data.decision_ts >= timestamps.iloc[b + embargo]]
    return train, val, test


def infer_policy(history: pd.DataFrame, *, models: tuple[str, ...] = ("logistic", "tree", "forest", "boosted", "neural"),
                 seed: int = 7, embargo: int = 1) -> InferenceResult:
    """Select on chronological validation only; evaluate untouched final segment once."""
    data = validate_history(history, authorized=True)
    n = len(data)
    if n < 150 or embargo < 0:
        raise ValueError("at least 150 observations and nonnegative embargo required")
    train, val, test = _chronological_parts(data, embargo)
    if min(len(train), len(val), len(test)) < 20 or train.action.nunique() < 2:
        raise ValueError("insufficient training or evaluation actions")
    xtrain, xval, xtest = (part[list(FEATURES)].to_numpy(float) for part in (train, val, test))
    fitted = []
    for name in models:
        model = _model(name, seed).fit(xtrain, train.action)
        classes = list(model.classes_)
        score = _metrics(val.action.to_numpy(), model.predict_proba(xval), classes)
        fitted.append((score["macro_f1"], name, model, classes, score))
    _, name, model, classes, score = max(fitted, key=lambda z: (z[0], -models.index(z[1])))
    holdout = _metrics(test.action.to_numpy(), model.predict_proba(xtest), classes)
    majority = str(train.action.mode().iloc[0])
    baseline = {"action": majority,
                "validation_accuracy": float((val.action == majority).mean()),
                "holdout_accuracy": float((test.action == majority).mean())}
    return InferenceResult(name, model, classes, score, holdout,
                           {"train_end": str(train.decision_ts.iloc[-1]),
                            "validation_start": str(val.decision_ts.iloc[0]),
                            "validation_end": str(val.decision_ts.iloc[-1]),
                            "holdout_start": str(test.decision_ts.iloc[0]),
                            "holdout_end": str(test.decision_ts.iloc[-1]), "embargo_timestamps": embargo}, seed,
                           baseline)


def _with_action_lags(data: pd.DataFrame, lags: int) -> tuple[pd.DataFrame, list[str]]:
    """Construct causal same-instrument action lags, including explicit UNKNOWN."""
    extended = data.copy()
    lag_features = []
    for lag in range(1, lags + 1):
        previous = extended.groupby("instrument", sort=False).action.shift(lag).fillna("UNKNOWN")
        for action in (*ACTIONS, "UNKNOWN"):
            name = f"prior_{lag}_{action}"
            extended[name] = (previous == action).astype(float)
            lag_features.append(name)
    return extended, lag_features


def infer_sequence_policy(history: pd.DataFrame, *, seed: int = 7, lags: int = 3) -> InferenceResult:
    """CPU behavioral cloning with strictly preceding same-instrument action lags.

    At decision time, past observed actions are available; missing history is encoded
    as UNKNOWN. This is a sequence baseline, not a recurrent neural network.
    """
    if lags < 1 or lags > 20:
        raise ValueError("lags must be between 1 and 20")
    data = validate_history(history, authorized=True)
    extended, lag_features = _with_action_lags(data, lags)
    train, val, test = _chronological_parts(extended, 1)
    if len(train) < 90 or min(len(val), len(test)) < 20 or train.action.nunique() < 2:
        raise ValueError("insufficient sequence history")
    columns = [*FEATURES, *lag_features]
    model = RandomForestClassifier(n_estimators=100, max_depth=8, min_samples_leaf=8,
                                   n_jobs=1, random_state=seed).fit(train[columns], train.action)
    classes = list(model.classes_)
    majority = str(train.action.mode().iloc[0])
    baseline = {"action": majority,
                "validation_accuracy": float((val.action == majority).mean()),
                "holdout_accuracy": float((test.action == majority).mean())}
    return InferenceResult("lagged_forest", model, classes,
                           _metrics(val.action.to_numpy(), model.predict_proba(val[columns]), classes),
                           _metrics(test.action.to_numpy(), model.predict_proba(test[columns]), classes),
                           {"train_end": str(train.decision_ts.iloc[-1]),
                            "validation_start": str(val.decision_ts.iloc[0]),
                            "validation_end": str(val.decision_ts.iloc[-1]),
                            "holdout_start": str(test.decision_ts.iloc[0]),
                            "holdout_end": str(test.decision_ts.iloc[-1]),
                            "embargo_timestamps": 1, "lagged_actions": lags}, seed, baseline)


def extract_rules(result: InferenceResult, history: pd.DataFrame, *, max_depth: int = 4) -> dict:
    """Fit a tree surrogate to the selected policy and measure validation fidelity."""
    data = validate_history(history, authorized=True)
    if result.model_name == "lagged_forest":
        data, lag_features = _with_action_lags(data, int(result.split["lagged_actions"]))
        feature_names = [*FEATURES, *lag_features]
    else:
        feature_names = list(FEATURES)
    train, val, _ = _chronological_parts(data, int(result.split["embargo_timestamps"]))
    xtrain, xval = train[feature_names].to_numpy(float), val[feature_names].to_numpy(float)
    source_train = train[feature_names] if result.model_name == "lagged_forest" else xtrain
    source_val = val[feature_names] if result.model_name == "lagged_forest" else xval
    surrogate = DecisionTreeClassifier(max_depth=max_depth, min_samples_leaf=12,
                                       random_state=result.seed).fit(xtrain, result.model.predict(source_train))
    fidelity = float(accuracy_score(result.model.predict(source_val), surrogate.predict(xval)))
    tree = surrogate.tree_
    rules = []
    def walk(node: int, conditions: list[dict]) -> None:
        if tree.feature[node] == _tree.TREE_UNDEFINED:
            action = surrogate.classes_[np.argmax(tree.value[node][0])]
            rules.append({"id": f"R{len(rules) + 1:03d}", "conditions": conditions,
                          "action": str(action), "sample_size": int(tree.n_node_samples[node])})
            return
        feature, threshold = feature_names[tree.feature[node]], float(tree.threshold[node])
        walk(tree.children_left[node], [*conditions, {"feature": feature, "operator": "<=", "value": threshold}])
        walk(tree.children_right[node], [*conditions, {"feature": feature, "operator": ">", "value": threshold}])
    walk(0, [])
    spec = {"schema_version": 1, "family": "observational_policy_surrogate", "direction": "action_reconstruction",
            "horizon": "decision_timestamp", "features": feature_names, "training_period": result.split,
            "source_model": result.model_name, "validation_fidelity": fidelity,
            "calibration": "unassessed", "provenance": sorted(data.provenance.unique().tolist()),
            "limitations": ["Observational action agreement is not trading profitability.",
                            "Hidden portfolio constraints can make policy non-identifiable."], "rules": rules}
    spec["version_hash"] = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]
    return spec


def regime_report(history: pd.DataFrame, *, min_n: int = 30) -> dict:
    """Descriptive observed outcome by as-of regime; never derives labels from future prices."""
    data = validate_history(history, authorized=True)
    if "net_return" not in data:
        return {"status": "outcomes_unavailable", "regimes": {}}
    if "regime_asof" in data and (_utc(data.regime_asof) > data.decision_ts).any():
        raise ValueError("future regime label")
    groups = data.groupby("regime_bull", sort=True)
    result = {}
    for label, part in groups:
        values = part.net_return.dropna().to_numpy(float)
        n = len(values)
        mean = float(values.mean()) if n else None
        se = float(values.std(ddof=1) / np.sqrt(n)) if n > 1 else None
        result[str(int(label))] = {"n": n, "validated": n >= min_n,
            "mean_net_return": mean, "hit_rate": float((values > 0).mean()) if n else None,
            "mean_95pct_normal_interval": [mean - 1.96 * se, mean + 1.96 * se] if se is not None else None}
    return {"status": "descriptive_only", "regimes": result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only inverse strategy research")
    parser.add_argument("--synthetic", choices=POLICIES)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--authorized", action="store_true")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--rows", type=int, default=900)
    parser.add_argument("--rules-out", type=Path)
    parser.add_argument("--report-out", type=Path)
    args = parser.parse_args(argv)
    if bool(args.synthetic) == bool(args.history):
        parser.error("choose exactly one of --synthetic or --history")
    data = (synthetic_history(args.synthetic, n=args.rows, seed=args.seed) if args.synthetic
            else import_history(args.history, authorized=args.authorized))
    result = infer_policy(data, seed=args.seed)
    rules = extract_rules(result, data)
    if args.rules_out:
        args.rules_out.write_text(json.dumps(rules, indent=2), encoding="utf-8")
    if args.report_out:
        from .trader_report import strategy_report
        report = strategy_report(result, rules, regime_report(data))
        args.report_out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"model": result.model_name, "split": result.split,
                      "validation": result.validation, "holdout": result.holdout,
                      "majority_baseline": result.majority_baseline,
                      "rule_count": len(rules["rules"]), "rule_fidelity": rules["validation_fidelity"],
                      "regime": regime_report(data), "warning": "Action agreement is not trading alpha."}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
