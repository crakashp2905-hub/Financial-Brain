"""Calibration-set uncertainty and market-regime abstention policy for index forecasts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from math import isfinite, log
from typing import Any

from .index_15m import check_probabilities

CLASSES = ("UP", "DOWN", "FLAT")
DEFAULT_BLOCKED_REGIMES = (
    "MACRO_EVENT_UNKNOWN", "EXPIRY_STATUS_UNKNOWN", "OPTION_SPREAD_UNKNOWN",
    "UNUSUAL_OPTION_SPREAD",
)


class IndexAbstentionError(ValueError):
    """Invalid calibration sample or point-in-time abstention decision."""


@dataclass(frozen=True)
class IndexAbstentionPolicy:
    min_confidence: float
    target_error_rate: float
    calibration_n: int
    calibration_errors: int
    calibration_error_upper_95: float
    calibration_manifest_sha256: str
    critical_regimes: tuple[str, ...]
    max_actions_per_session: int

    @property
    def sha256(self) -> str:
        payload = json.dumps({
            "min_confidence": self.min_confidence,
            "target_error_rate": self.target_error_rate,
            "calibration_n": self.calibration_n,
            "calibration_errors": self.calibration_errors,
            "calibration_error_upper_95": self.calibration_error_upper_95,
            "calibration_manifest_sha256": self.calibration_manifest_sha256,
            "critical_regimes": self.critical_regimes,
            "max_actions_per_session": self.max_actions_per_session,
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


def _probabilities(row: dict[str, Any]) -> dict[str, float]:
    raw = {name: row.get(f"p_{name.lower()}") for name in CLASSES}
    probabilities = {}
    for name, value in raw.items():
        if not isinstance(value, (int, float)) or not isfinite(value):
            raise IndexAbstentionError("UP/DOWN/FLAT probabilities must be finite")
        probabilities[name] = float(value)
    try:
        check_probabilities(probabilities)
    except ValueError as error:
        raise IndexAbstentionError("probabilities must be valid and sum to one") from error
    return probabilities


def _wilson_upper(errors: int, n: int) -> float:
    z = 1.96
    p = errors / n
    denom = 1 + z * z / n
    center = p + z * z / (2 * n)
    radius = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return min(1.0, (center + radius) / denom)


def calibrate_index_abstention(calibration_rows: list[dict[str, Any]], *,
                               calibration_manifest_sha256: str,
                               target_error_rate: float = 0.10,
                               min_calibration_predictions: int = 100,
                               max_actions_per_session: int = 5,
                               critical_regimes: tuple[str, ...] = DEFAULT_BLOCKED_REGIMES
                               ) -> IndexAbstentionPolicy:
    """Choose the broadest confidence cutoff whose Wilson error upper bound meets target.

    Only a caller-designated calibration set is accepted. The manifest hash binds the
    split provenance; the caller must keep these rows disjoint from model fitting and test.
    """
    if (len(calibration_manifest_sha256) != 64
            or any(c not in "0123456789abcdef" for c in calibration_manifest_sha256)):
        raise IndexAbstentionError("calibration manifest must be lowercase SHA-256")
    if (not 0 < target_error_rate < 1 or type(min_calibration_predictions) is not int
            or min_calibration_predictions < 1 or type(max_actions_per_session) is not int
            or max_actions_per_session < 1):
        raise IndexAbstentionError("invalid calibration error target or action limit")
    if not calibration_rows:
        raise IndexAbstentionError("calibration rows are required")
    prepared = []
    keys = set()
    for row in calibration_rows:
        key, actual = row.get("interval_end"), row.get("actual")
        if not isinstance(key, str) or not key or key in keys or actual not in CLASSES:
            raise IndexAbstentionError("calibration interval IDs must be unique with valid labels")
        keys.add(key)
        probs = _probabilities(row)
        predicted = max(CLASSES, key=lambda name: (probs[name], -CLASSES.index(name)))
        prepared.append((max(probs.values()), int(predicted != actual)))

    chosen = None
    for threshold in sorted({confidence for confidence, _ in prepared}):
        selected = [error for confidence, error in prepared if confidence >= threshold]
        if len(selected) < min_calibration_predictions:
            continue
        errors = sum(selected)
        upper = _wilson_upper(errors, len(selected))
        if upper <= target_error_rate:
            chosen = (threshold, len(selected), errors, upper)
            break
    if chosen is None:
        raise IndexAbstentionError("calibration set cannot meet the target error bound")
    threshold, n, errors, upper = chosen
    return IndexAbstentionPolicy(float(threshold), target_error_rate, n, errors, upper,
                                 calibration_manifest_sha256, tuple(sorted(set(critical_regimes))),
                                 max_actions_per_session)


def apply_index_abstention_policy(records: list[dict[str, Any]],
                                  policy: IndexAbstentionPolicy) -> dict[str, Any]:
    """Apply the frozen policy chronologically; never use later session forecasts to rank earlier ones."""
    prepared = []
    seen = set()
    for row in records:
        interval = row.get("interval_end")
        session = row.get("session")
        decision = row.get("decision_at")
        if not isinstance(interval, str) or interval in seen or not isinstance(session, str):
            raise IndexAbstentionError("decision intervals must be unique and have a session")
        seen.add(interval)
        if not isinstance(decision, str):
            raise IndexAbstentionError("decision_at must be an ISO timestamp")
        try:
            decision_time = datetime.fromisoformat(decision)
        except ValueError as error:
            raise IndexAbstentionError("decision_at must be an ISO timestamp") from error
        if decision_time.tzinfo is None or decision_time.utcoffset() is None:
            raise IndexAbstentionError("decision_at must be timezone-aware")
        regimes = row.get("regimes", [])
        if not isinstance(regimes, (list, tuple)) or any(not isinstance(tag, str) for tag in regimes):
            raise IndexAbstentionError("regimes must be a sequence of string tags")
        prepared.append((session, decision_time, row, _probabilities(row), set(regimes)))
    if [(session, decision) for session, decision, *_ in prepared] != sorted(
            (session, decision) for session, decision, *_ in prepared):
        raise IndexAbstentionError("policy rows must be chronological")

    action_counts: dict[str, int] = {}
    decisions = []
    for session, decision_time, row, probabilities, regimes in prepared:
        ranked = sorted(probabilities.items(), key=lambda item: (-item[1], CLASSES.index(item[0])))
        predicted, confidence = ranked[0]
        margin = confidence - ranked[1][1]
        entropy = -sum(value * log(value) for value in probabilities.values() if value > 0) / log(3)
        reason = None
        if regimes.intersection(policy.critical_regimes):
            reason = "CRITICAL_MARKET_REGIME"
        elif predicted == "FLAT":
            reason = "NO_DIRECTIONAL_SIGNAL"
        elif confidence < policy.min_confidence:
            reason = "LOW_CALIBRATED_CONFIDENCE"
        elif action_counts.get(session, 0) >= policy.max_actions_per_session:
            reason = "SESSION_ACTION_LIMIT"
        action = "ABSTAIN" if reason else "ISSUE_DIRECTIONAL_RESEARCH_SIGNAL"
        if reason is None:
            action_counts[session] = action_counts.get(session, 0) + 1
        decisions.append({"interval_end": row["interval_end"], "decision_at": decision_time.isoformat(),
                          "session": session, "action": action, "predicted_class": predicted,
                          "confidence": confidence, "margin": margin, "normalized_entropy": entropy,
                          "regimes": sorted(regimes), "reason": reason,
                          "session_actions_so_far": action_counts.get(session, 0),
                          "policy_sha256": policy.sha256})
    issued = sum(row["action"] == "ISSUE_DIRECTIONAL_RESEARCH_SIGNAL" for row in decisions)
    return {"schema": "fb.index_abstention_decisions.v1", "policy_sha256": policy.sha256,
            "intervals": len(decisions), "issued": issued,
            "abstained": len(decisions) - issued,
            "issued_fraction": issued / len(decisions) if decisions else 0.0,
            "actions_per_session": action_counts, "decisions": decisions,
            "policy_is_research_only": True}
