"""Decision-quality scorecard for three-class index forecasts.

Rows are realized forecast-issued decisions, not strategy P&L. Confidence intervals
resample whole sessions to retain within-session overlap; cross-session dependence can
still make the reported intervals optimistic, so they are diagnostics rather than proof.
"""
from __future__ import annotations

import random
from collections import Counter
from math import isfinite, log
from typing import Any

from .index_15m import check_probabilities

CLASSES = ("UP", "DOWN", "FLAT")
ORDERED_CLASSES = ("DOWN", "FLAT", "UP")


def _quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _validated(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        raise ValueError("at least one resolved forecast row is required")
    out = []
    for row in rows:
        actual = row.get("actual")
        session = row.get("session")
        raw_probabilities = {name: row.get(f"p_{name.lower()}") for name in CLASSES}
        if actual not in CLASSES or not isinstance(session, str) or not session:
            raise ValueError("each row needs a supported actual label and session key")
        probabilities: dict[str, float] = {}
        for name in CLASSES:
            value = raw_probabilities[name]
            if not isinstance(value, (int, float)) or not isfinite(value):
                raise ValueError("each row needs finite UP/DOWN/FLAT probabilities")
            probabilities[name] = float(value)
        check_probabilities(probabilities)
        expected = row.get("expected_move_abs")
        realized = row.get("realized_return")
        if expected is not None and (not isinstance(expected, (int, float)) or not isfinite(expected)
                                     or expected < 0):
            raise ValueError("expected absolute move must be finite and nonnegative")
        if realized is not None and (not isinstance(realized, (int, float)) or not isfinite(realized)):
            raise ValueError("realized return must be finite")
        out.append({**row, **{f"p_{name.lower()}": probabilities[name] for name in CLASSES}})
    return out


def _point_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    actual_counts = Counter(row["actual"] for row in rows)
    confusion = {actual: {predicted: 0 for predicted in CLASSES} for actual in CLASSES}
    briers: list[float] = []
    log_losses: list[float] = []
    crps_values: list[float] = []
    hits = 0
    directional = 0
    move_errors: list[float] = []

    for row in rows:
        probabilities = {name: row[f"p_{name.lower()}"] for name in CLASSES}
        actual = row["actual"]
        # Stable tie order is part of the scorecard contract.
        predicted = max(CLASSES, key=lambda name: (probabilities[name], -CLASSES.index(name)))
        confusion[actual][predicted] += 1
        briers.append(sum((probabilities[name] - float(actual == name)) ** 2 for name in CLASSES))
        log_losses.append(-log(max(probabilities[actual], 1e-15)))
        cdf = 0.0
        crps = 0.0
        for name in ORDERED_CLASSES[:-1]:
            cdf += probabilities[name]
            actual_cdf = float(ORDERED_CLASSES.index(actual) <= ORDERED_CLASSES.index(name))
            crps += (cdf - actual_cdf) ** 2
        crps_values.append(crps)
        if actual != "FLAT" and predicted != "FLAT":
            directional += 1
            hits += int(actual == predicted)
        expected, realized = row.get("expected_move_abs"), row.get("realized_return")
        if expected is not None and realized is not None:
            move_errors.append(abs(float(expected) - abs(float(realized))))

    per_class = {}
    for name in CLASSES:
        tp = confusion[name][name]
        predicted_count = sum(confusion[actual][name] for actual in CLASSES)
        support = actual_counts[name]
        precision = tp / predicted_count if predicted_count else None
        recall = tp / support if support else None
        f1 = (2 * precision * recall / (precision + recall)
              if precision is not None and recall is not None and precision + recall else None)
        class_probabilities: list[float] = [row[f"p_{name.lower()}"] for row in rows]
        bins: list[dict[str, Any]] = []
        for lower in range(0, 10):
            selected: list[tuple[float, bool]] = [(probability, row["actual"] == name)
                        for probability, row in zip(class_probabilities, rows)
                        if lower / 10 <= probability < (lower + 1) / 10
                        or (lower == 9 and probability == 1.0)]
            if selected:
                bins.append({"lower": lower / 10, "upper": (lower + 1) / 10,
                             "n": len(selected),
                             "mean_probability": sum(p for p, _ in selected) / len(selected),
                             "observed_rate": sum(float(y) for _, y in selected) / len(selected)})
        ece = sum(bucket["n"] / n * abs(bucket["mean_probability"] - bucket["observed_rate"])
                  for bucket in bins)
        mean_probability = sum(class_probabilities) / n
        observed_rate = support / n
        per_class[name] = {"support": support, "precision": precision, "recall": recall,
                           "f1": f1, "mean_probability": mean_probability,
                           "observed_rate": observed_rate,
                           "calibration_gap": mean_probability - observed_rate,
                           "ece_10_bins": ece, "reliability_bins": bins}

    accuracy = sum(confusion[name][name] for name in CLASSES) / n
    result = {
        "n": n, "accuracy": accuracy,
        "majority_class_accuracy": max(actual_counts.values()) / n,
        "brier": sum(briers) / n,
        "log_loss": sum(log_losses) / n,
        "crps": sum(crps_values) / n,
        "directional_n": directional,
        "directional_accuracy": hits / directional if directional else None,
        "directional_skill_vs_50pct": hits / directional - 0.5 if directional else None,
        "directional_coverage": (directional / sum(actual_counts[name] for name in ("UP", "DOWN"))
                                 if actual_counts["UP"] + actual_counts["DOWN"] else None),
        "expected_move_n": len(move_errors),
        "expected_move_mae": sum(move_errors) / len(move_errors) if move_errors else None,
        "confusion": confusion, "per_class": per_class,
    }
    return result


def score_index_forecasts(rows: list[dict[str, Any]], *, bootstrap_reps: int = 500,
                          seed: int = 7) -> dict[str, Any]:
    """Score resolved forecasts and provide session-cluster percentile intervals.

    Required row fields: ``session``, ``actual``, ``p_up``, ``p_down``, ``p_flat``.
    Optional ``expected_move_abs`` and ``realized_return`` produce expected-move MAE.
    Only resolved outcomes enter these metrics; outage/abstention accounting belongs in
    the forecast coverage ledger and must not be imputed as a successful prediction.
    """
    if type(bootstrap_reps) is not int or bootstrap_reps < 0:
        raise ValueError("bootstrap_reps must be a nonnegative integer")
    clean = _validated(rows)
    point = _point_metrics(clean)
    sessions: dict[str, list[dict[str, Any]]] = {}
    for row in clean:
        sessions.setdefault(row["session"], []).append(row)
    interval_metrics = ("accuracy", "brier", "expected_move_mae")
    intervals: dict[str, dict[str, float | None]] = {}
    if len(sessions) < 2 or bootstrap_reps == 0:
        intervals = {name: {"lower_95": None, "upper_95": None} for name in interval_metrics}
    else:
        rng = random.Random(seed)
        session_rows = list(sessions.values())
        boot_values: dict[str, list[float]] = {name: [] for name in interval_metrics}
        for _ in range(bootstrap_reps):
            sample = []
            for _ in session_rows:
                sample.extend(rng.choice(session_rows))
            scored = _point_metrics(sample)
            for name in interval_metrics:
                value = scored[name]
                if value is not None:
                    boot_values[name].append(float(value))
        intervals = {name: {"lower_95": _quantile(boot_values[name], 0.025),
                            "upper_95": _quantile(boot_values[name], 0.975)}
                     for name in interval_metrics}
    return {"schema": "fb.index_forecast_scorecard.v1", "metrics": point,
            "confidence_intervals": intervals,
            "interval_method": "session-cluster percentile bootstrap; cross-session dependence remains",
            "resolved_rows_only": True,
            "unresolved_rows_are_not_scored_as_success": True}
