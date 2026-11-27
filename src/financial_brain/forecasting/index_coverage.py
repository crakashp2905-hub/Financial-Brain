"""Coverage-aware scoring that keeps missed intervals and unresolved outcomes visible."""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from .index_metrics import score_index_forecasts


class ForecastCoverageError(ValueError):
    """Invalid expected interval, issue or realized outcome inventory."""


def score_expected_intervals(expected_intervals: list[str], issues: list[dict[str, Any]],
                             outcomes: list[dict[str, Any]], *,
                             bootstrap_reps: int = 500, seed: int = 7) -> dict[str, Any]:
    """Reconcile the full expected schedule with issues and realized index outcomes.

    ISSUED and ABSTAINED rows with resolved labels enter probabilistic forecast scoring;
    only ISSUED rows enter the actionable subset. MISSED and NO_ISSUE rows remain explicit
    coverage failures even when their market outcome is known. Missing outcomes never count
    as model successes.
    """
    if not expected_intervals or len(set(expected_intervals)) != len(expected_intervals):
        raise ForecastCoverageError("expected interval list must be nonempty and unique")
    expected = set(expected_intervals)

    def unique_by_interval(rows: list[dict[str, Any]], kind: str) -> dict[str, dict[str, Any]]:
        result = {}
        for row in rows:
            key = row.get("interval_end")
            if not isinstance(key, str) or not key:
                raise ForecastCoverageError(f"each {kind} row needs an interval_end key")
            if key in result:
                raise ForecastCoverageError(f"duplicate {kind} row for {key}")
            result[key] = row
        return result

    issue_map = unique_by_interval(issues, "issue")
    outcome_map = unique_by_interval(outcomes, "outcome")
    extra_issues = sorted(set(issue_map) - expected)
    extra_outcomes = sorted(set(outcome_map) - expected)
    counts: Counter[str] = Counter()
    outcome_annotations: Counter[str] = Counter()
    all_forecasts: list[dict[str, Any]] = []
    actionable: list[dict[str, Any]] = []
    intervals: list[dict[str, str]] = []

    for key in expected_intervals:
        issue = issue_map.get(key)
        outcome = outcome_map.get(key)
        if issue is None:
            state = "NO_ISSUE"
        else:
            state = str(issue.get("status"))
            if state not in {"ISSUED", "ABSTAINED", "MISSED"}:
                raise ForecastCoverageError(f"unsupported issue status for {key}: {state}")
        counts[state] += 1
        if outcome is not None and issue is None:
            outcome_annotations["NO_ISSUE_WITH_OUTCOME"] += 1
        if outcome is not None and issue is not None and issue.get("status") == "MISSED":
            outcome_annotations["MISSED_WITH_OUTCOME"] += 1
        if outcome is None:
            outcome_annotations["UNRESOLVED_OUTCOME"] += 1

        if (issue is not None and outcome is not None
                and issue.get("status") in {"ISSUED", "ABSTAINED"}):
            probabilities = issue.get("probabilities")
            if probabilities is None and issue.get("probabilities_json"):
                probabilities = json.loads(issue["probabilities_json"])
            if not isinstance(probabilities, dict):
                raise ForecastCoverageError(f"{issue.get('status')} row lacks probabilities for {key}")
            row = {"session": str(issue.get("session_date") or key[:10]),
                   "actual": outcome.get("actual"),
                   "p_up": probabilities.get("UP"), "p_down": probabilities.get("DOWN"),
                   "p_flat": probabilities.get("FLAT"),
                   "expected_move_abs": issue.get("expected_move_abs"),
                   "realized_return": outcome.get("realized_return")}
            all_forecasts.append(row)
            if issue.get("status") == "ISSUED":
                actionable.append(row)
        intervals.append({"interval_end": key, "state": state,
                          "outcome": "RESOLVED" if outcome is not None else "UNRESOLVED"})

    expected_n = len(expected_intervals)
    scored_report = (score_index_forecasts(all_forecasts, bootstrap_reps=bootstrap_reps, seed=seed)
                     if all_forecasts else None)
    actionable_report = (score_index_forecasts(actionable, bootstrap_reps=bootstrap_reps, seed=seed)
                         if actionable else None)
    return {
        "schema": "fb.index_forecast_coverage_score.v1",
        "expected_intervals": expected_n,
        "issue_coverage": (expected_n - counts["NO_ISSUE"]) / expected_n,
        "actionable_issue_coverage": sum(1 for row in issues
                                         if row.get("interval_end") in expected
                                         and row.get("status") == "ISSUED") / expected_n,
        "abstention_rate": sum(1 for row in issues
                               if row.get("interval_end") in expected
                               and row.get("status") == "ABSTAINED") / expected_n,
        "status_counts": dict(sorted(counts.items())),
        "outcome_annotations": dict(sorted(outcome_annotations.items())),
        "scored_issued_and_abstained": scored_report,
        "scored_actionable_issued_only": actionable_report,
        "extra_issue_intervals": extra_issues,
        "extra_outcome_intervals": extra_outcomes,
        "intervals": intervals,
        "missing_outcomes_are_not_successes": True,
    }
