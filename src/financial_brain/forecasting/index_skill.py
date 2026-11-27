"""Predictive-only skill gate, compared with paired index-forecast baselines."""
from __future__ import annotations

import random
from collections import defaultdict
from math import isfinite
from typing import Any

from .index_15m import check_probabilities

CLASSES = ("UP", "DOWN", "FLAT")


class IndexSkillError(ValueError):
    """Invalid or incomparable candidate/baseline forecast cohorts."""


def _rows_by_interval(rows: list[dict[str, Any]], name: str) -> dict[str, dict[str, Any]]:
    indexed = {}
    for row in rows:
        key = row.get("interval_end")
        if not isinstance(key, str) or not key or key in indexed:
            raise IndexSkillError(f"{name} interval IDs must be present and unique")
        actual = row.get("actual")
        session = row.get("session")
        raw_probabilities = {label: row.get(f"p_{label.lower()}") for label in CLASSES}
        if actual not in CLASSES or not isinstance(session, str) or not session:
            raise IndexSkillError(f"{name} rows need actual class and session")
        probabilities: dict[str, float] = {}
        for label in CLASSES:
            value = raw_probabilities[label]
            if not isinstance(value, (int, float)) or not isfinite(value):
                raise IndexSkillError(f"{name} rows need finite class probabilities")
            probabilities[label] = float(value)
        try:
            check_probabilities(probabilities)
        except (ValueError, TypeError) as error:
            raise IndexSkillError(f"{name} probabilities are invalid") from error
        indexed[key] = row
    return indexed


def _brier(row: dict[str, Any]) -> float:
    return sum((float(row[f"p_{label.lower()}"]) - float(row["actual"] == label)) ** 2
               for label in CLASSES)


def _quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def predictive_skill_gate(candidate_rows: list[dict[str, Any]],
                          baseline_rows: dict[str, list[dict[str, Any]]], *,
                          minimum_rows: int = 500, minimum_sessions: int = 30,
                          minimum_brier_skill: float = 0.01,
                          bootstrap_reps: int = 1000, seed: int = 7) -> dict[str, Any]:
    """Require paired Brier improvement over every baseline with session-bootstrap CI.

    This gate accepts no options P&L or fill inputs. PASS means predictive research skill
    only; it never authorizes a strategy, options trade or capital promotion.
    """
    if not baseline_rows:
        raise IndexSkillError("at least one hard baseline is required")
    if (minimum_rows < 1 or minimum_sessions < 2 or not isfinite(minimum_brier_skill)
            or minimum_brier_skill < 0 or type(bootstrap_reps) is not int or bootstrap_reps < 100):
        raise IndexSkillError("invalid predictive-skill gate thresholds")
    candidate = _rows_by_interval(candidate_rows, "candidate")
    if not candidate:
        raise IndexSkillError("candidate forecast rows cannot be empty")
    sessions = {row["session"] for row in candidate.values()}
    comparable = len(candidate) >= minimum_rows and len(sessions) >= minimum_sessions
    results: dict[str, Any] = {}
    passed = comparable
    rng = random.Random(seed)
    for baseline_name, baseline_records in sorted(baseline_rows.items()):
        if not baseline_name:
            raise IndexSkillError("baseline names are required")
        baseline = _rows_by_interval(baseline_records, baseline_name)
        if set(candidate) != set(baseline):
            results[baseline_name] = {"status": "INCOMPARABLE", "candidate_n": len(candidate),
                                      "baseline_n": len(baseline)}
            passed = False
            continue
        if any(candidate[key]["actual"] != baseline[key]["actual"]
               or candidate[key]["session"] != baseline[key]["session"] for key in candidate):
            raise IndexSkillError("candidate and baseline labels/sessions must match by interval")
        by_session: dict[str, list[float]] = defaultdict(list)
        for key in candidate:
            by_session[candidate[key]["session"]].append(_brier(baseline[key]) - _brier(candidate[key]))
        paired = [value for values in by_session.values() for value in values]
        skill = sum(paired) / len(paired) if paired else 0.0
        boot_means = []
        session_values = list(by_session.values())
        if len(session_values) >= 2:
            for _ in range(bootstrap_reps):
                sample = []
                for _ in session_values:
                    sample.extend(rng.choice(session_values))
                boot_means.append(sum(sample) / len(sample))
        lower = _quantile(boot_means, 0.025)
        upper = _quantile(boot_means, 0.975)
        this_pass = (comparable and skill >= minimum_brier_skill
                     and lower is not None and lower > 0)
        passed = passed and this_pass
        results[baseline_name] = {
            "status": "PASS" if this_pass else "FAIL",
            "n": len(paired), "sessions": len(by_session),
            "candidate_brier": sum(_brier(candidate[key]) for key in candidate) / len(candidate),
            "baseline_brier": sum(_brier(baseline[key]) for key in baseline) / len(baseline),
            "brier_skill": skill,
            "brier_skill_ci_95": {"lower": lower, "upper": upper},
            "minimum_brier_skill": minimum_brier_skill,
            "bootstrap_method": "paired session-cluster percentile",
        }
    return {"schema": "fb.index_predictive_skill_gate.v1",
            "status": "PREDICTIVE_SKILL_VERIFIED" if passed else "NOT_VERIFIED",
            "scope": "forecast prediction only; no strategy or capital authorization",
            "options_pnl_considered": False,
            "candidate_n": len(candidate), "candidate_sessions": len(sessions),
            "minimum_rows": minimum_rows, "minimum_sessions": minimum_sessions,
            "comparisons": results}
