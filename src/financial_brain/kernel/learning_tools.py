"""Learning baselines, challengers, calibration and reproducibility (FB-0434, FB-0435, FB-0481, FB-0482, FB-0483).

* ``fit_simple_linear`` is a one-feature least-squares baseline. ``challenger_wins`` promotes a challenger only if it
  beats the incumbent on held-out error. A tie keeps the incumbent.
* ``majority_classifier`` is the honest classification baseline: it always predicts the training majority.
* ``brier_score`` and ``calibration_bins`` measure probability calibration. They are kept apart from execution utility.
* ``optional_dependency_present`` checks for an optional module without importing it.
* ``certificate`` hashes inputs and version strings so a run can be checked later.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from statistics import mean


def fit_simple_linear(x: Sequence[float], y: Sequence[float]) -> tuple[float, float]:
    if len(x) != len(y) or len(x) < 2:
        raise ValueError("need at least two aligned points")
    mx, my = mean(x), mean(y)
    sxx = sum((xi - mx) ** 2 for xi in x)
    if sxx == 0:
        raise ValueError("feature has no variation")
    slope = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y, strict=True)) / sxx
    return slope, my - slope * mx


def mse(pred: Sequence[float], actual: Sequence[float]) -> float:
    if len(pred) != len(actual) or not pred:
        raise ValueError("predictions and actuals must align and be non-empty")
    return mean((p - a) ** 2 for p, a in zip(pred, actual, strict=True))


def challenger_wins(incumbent_error: float, challenger_error: float) -> bool:
    return challenger_error < incumbent_error


def majority_classifier(train_labels: Sequence[int]) -> int:
    if not train_labels:
        raise ValueError("no training labels")
    return Counter(train_labels).most_common(1)[0][0]


def brier_score(probs: Sequence[float], outcomes: Sequence[int]) -> float:
    if len(probs) != len(outcomes) or not probs:
        raise ValueError("probabilities and outcomes must align and be non-empty")
    return mean((p - o) ** 2 for p, o in zip(probs, outcomes, strict=True))


def calibration_bins(probs: Sequence[float], outcomes: Sequence[int], bins: int) -> list[tuple[float, float, int]]:
    """(mean predicted, observed rate, count) per equal-width bin. Empty bins are omitted."""
    buckets: dict[int, list[tuple[float, int]]] = {}
    for p, o in zip(probs, outcomes, strict=True):
        idx = min(int(p * bins), bins - 1)
        buckets.setdefault(idx, []).append((p, o))
    out: list[tuple[float, float, int]] = []
    for idx in sorted(buckets):
        items = buckets[idx]
        out.append((mean(p for p, _ in items), mean(o for _, o in items), len(items)))
    return out


def optional_dependency_present(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def certificate(inputs: Mapping[str, str], versions: Mapping[str, str]) -> str:
    body = json.dumps({"inputs": dict(inputs), "versions": dict(versions)}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()
