"""Small supervised index-direction baselines, fitted only on caller-supplied training rows."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any

CLASSES = ("UP", "DOWN", "FLAT")


class IndexModelError(ValueError):
    """Invalid training or prediction data for the compact index classifiers."""


@dataclass
class TrainedIndexClassifier:
    name: str
    feature_names: tuple[str, ...]
    hyperparameters: dict[str, Any]
    estimator: Any


def _validate_matrix(features: list[list[float]], feature_names: list[str], *, minimum_rows: int) -> None:
    if len(features) < minimum_rows or not feature_names:
        raise IndexModelError(f"at least {minimum_rows} training rows and named features are required")
    if any(not name for name in feature_names) or len(set(feature_names)) != len(feature_names):
        raise IndexModelError("feature names must be nonempty and unique")
    if any(len(row) != len(feature_names) for row in features):
        raise IndexModelError("each feature row must match the declared feature count")
    if any(not isfinite(value) for row in features for value in row):
        raise IndexModelError("training features must be finite; imputation must be fitted inside training")


def fit_index_classifiers(features: list[list[float]], labels: list[str],
                          feature_names: list[str], *, seed: int = 7
                          ) -> dict[str, TrainedIndexClassifier]:
    """Fit regularized logistic and gradient-boosted models from training rows only.

    The fixed conservative configurations are baselines, not tuned winners. Callers must
    use chronological/purged folds and score them against the hard baselines before
    considering more complex sequence models.
    """
    _validate_matrix(features, feature_names, minimum_rows=20)
    if len(labels) != len(features) or any(label not in CLASSES for label in labels):
        raise IndexModelError("training labels must align with rows and be UP/DOWN/FLAT")
    if len(set(labels)) < 2:
        raise IndexModelError("training labels need at least two observed classes")
    if type(seed) is not int:
        raise IndexModelError("seed must be an integer")

    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    logistic_params = {"C": 1.0, "solver": "lbfgs", "max_iter": 2000,
                       "class_weight": None, "random_state": seed}
    logistic = make_pipeline(StandardScaler(), LogisticRegression(**logistic_params))
    logistic.fit(features, labels)

    boosted_params = {"learning_rate": 0.05, "max_iter": 100, "max_leaf_nodes": 7,
                      "l2_regularization": 1.0, "min_samples_leaf": 10,
                      "random_state": seed}
    boosted = HistGradientBoostingClassifier(**boosted_params)
    boosted.fit(features, labels)
    return {
        "regularized_logistic": TrainedIndexClassifier(
            "regularized_logistic", tuple(feature_names), logistic_params, logistic),
        "hist_gradient_boosting": TrainedIndexClassifier(
            "hist_gradient_boosting", tuple(feature_names), boosted_params, boosted),
    }


def predict_index_probabilities(model: TrainedIndexClassifier,
                                features: list[list[float]]) -> list[dict[str, float]]:
    """Return complete UP/DOWN/FLAT probabilities, inserting zero for absent train classes."""
    _validate_matrix(features, list(model.feature_names), minimum_rows=1)
    raw = model.estimator.predict_proba(features)
    observed_classes = list(model.estimator.classes_)
    out = []
    for row in raw:
        probabilities = {name: 0.0 for name in CLASSES}
        for name, value in zip(observed_classes, row, strict=True):
            probabilities[str(name)] = float(value)
        total = sum(probabilities.values())
        if total <= 0 or any(not isfinite(value) or value < 0 for value in probabilities.values()):
            raise IndexModelError("classifier emitted invalid class probabilities")
        out.append({name: value / total for name, value in probabilities.items()})
    return out
