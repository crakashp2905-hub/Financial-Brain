"""Same-cohort, pre-frozen allocation-method comparison for paper research."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from statistics import stdev
from types import MappingProxyType
from typing import Mapping


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_METHODS = frozenset({
    "EQUAL_RISK", "EQUAL_WEIGHT", "VOLATILITY_TARGETED",
    "ROBUST_MEAN_VARIANCE", "REINFORCEMENT_LEARNING",
})


class AllocationBenchmarkError(ValueError):
    """Invalid frozen policy, evaluation cohort or performance panel."""


class AllocationMethod(StrEnum):
    EQUAL_RISK = "EQUAL_RISK"
    EQUAL_WEIGHT = "EQUAL_WEIGHT"
    VOLATILITY_TARGETED = "VOLATILITY_TARGETED"
    ROBUST_MEAN_VARIANCE = "ROBUST_MEAN_VARIANCE"
    REINFORCEMENT_LEARNING = "REINFORCEMENT_LEARNING"


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AllocationBenchmarkError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _check_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise AllocationBenchmarkError(f"{label} must be a lowercase SHA-256")


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class FrozenAllocationPolicy:
    method: AllocationMethod
    policy_version: str
    frozen_at: datetime
    training_window_end: datetime
    training_data_sha256: str
    parameters_sha256: str
    weights: Mapping[str, float]

    def __post_init__(self) -> None:
        if not isinstance(self.method, AllocationMethod) or not self.policy_version.strip():
            raise AllocationBenchmarkError("typed allocation method and policy version are required")
        frozen = _utc(self.frozen_at, "policy frozen_at")
        training_end = _utc(self.training_window_end, "training_window_end")
        if training_end > frozen:
            raise AllocationBenchmarkError("policy cannot be frozen before its training window ends")
        _check_hash(self.training_data_sha256, "training data hash")
        _check_hash(self.parameters_sha256, "parameter hash")
        if not self.weights or any(not name.strip() or not math.isfinite(weight) or weight < 0
                                   for name, weight in self.weights.items()):
            raise AllocationBenchmarkError("frozen policy needs finite, non-negative candidate weights")
        if sum(self.weights.values()) > 1.0 + 1e-9:
            raise AllocationBenchmarkError("long-only allocation weights cannot exceed full capital")
        object.__setattr__(self, "frozen_at", frozen)
        object.__setattr__(self, "training_window_end", training_end)
        object.__setattr__(self, "weights", MappingProxyType(dict(sorted(self.weights.items()))))

    def digest(self) -> str:
        return _hash({"method": self.method.value, "policy_version": self.policy_version,
                      "frozen_at": self.frozen_at.isoformat(),
                      "training_window_end": self.training_window_end.isoformat(),
                      "training_data_sha256": self.training_data_sha256,
                      "parameters_sha256": self.parameters_sha256,
                      "weights": dict(self.weights)})


@dataclass(frozen=True, slots=True)
class AllocationEvaluationSession:
    session_id: str
    session_at: datetime
    net_returns: Mapping[str, float]
    cash_net_return: float

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise AllocationBenchmarkError("session ID is required")
        object.__setattr__(self, "session_at", _utc(self.session_at, "session_at"))
        if not self.net_returns or any(not key.strip() or not math.isfinite(value) or value <= -1
                                       for key, value in self.net_returns.items()):
            raise AllocationBenchmarkError("same-session net returns must be finite and above -100%")
        if not math.isfinite(self.cash_net_return) or self.cash_net_return <= -1:
            raise AllocationBenchmarkError("cash net return must be finite and above -100%")
        object.__setattr__(self, "net_returns", MappingProxyType(dict(sorted(self.net_returns.items()))))


@dataclass(frozen=True, slots=True)
class AllocationBenchmarkReport:
    cohort_sha256: str
    evaluation_sessions: int
    method_metrics: Mapping[str, Mapping[str, object]]
    report_sha256: str
    promotion_allowed: bool = False


def compare_allocation_methods(*, policies: tuple[FrozenAllocationPolicy, ...],
                               sessions: tuple[AllocationEvaluationSession, ...],
                               evaluation_data_sha256: str) -> AllocationBenchmarkReport:
    """Score five methods on identical net-return sessions after policies are frozen.

    Return rows are caller-supplied net-of-cost observations. All five methods must use the
    same candidate set and training-data snapshot; the RL row is a frozen policy artifact,
    not a retraining run on the evaluation cohort.
    """
    _check_hash(evaluation_data_sha256, "evaluation data hash")
    if len({row.method for row in policies}) != len(policies):
        raise AllocationBenchmarkError("allocation methods must be unique")
    present = {row.method.value for row in policies}
    if present != _REQUIRED_METHODS:
        missing, extra = sorted(_REQUIRED_METHODS - present), sorted(present - _REQUIRED_METHODS)
        raise AllocationBenchmarkError(f"benchmark requires all five methods; missing={missing}, extra={extra}")
    if not sessions:
        raise AllocationBenchmarkError("evaluation session panel cannot be empty")
    session_ids = [row.session_id for row in sessions]
    session_times = [row.session_at for row in sessions]
    if len(set(session_ids)) != len(session_ids) or len(set(session_times)) != len(session_times):
        raise AllocationBenchmarkError("evaluation sessions must be unique")
    if session_times != sorted(session_times):
        raise AllocationBenchmarkError("evaluation sessions must be chronological")
    candidate_ids = set(sessions[0].net_returns)
    ordered_candidate_ids = tuple(sorted(candidate_ids))
    if any(set(row.net_returns) != candidate_ids for row in sessions):
        raise AllocationBenchmarkError("every session must include every frozen candidate, including cash outcomes")
    if any(set(row.weights) != candidate_ids for row in policies):
        raise AllocationBenchmarkError("each method must allocate the same frozen candidate set")
    first_session = session_times[0]
    if any(row.frozen_at >= first_session for row in policies):
        raise AllocationBenchmarkError("every allocation policy must be frozen before the first evaluation session")
    if len({row.training_data_sha256 for row in policies}) != 1:
        raise AllocationBenchmarkError("all allocation methods must use the same training-data snapshot")
    if next(iter({row.training_data_sha256 for row in policies})) == evaluation_data_sha256:
        raise AllocationBenchmarkError("training and evaluation dataset hashes must be distinct")

    metrics: dict[str, Mapping[str, object]] = {}
    for policy in sorted(policies, key=lambda row: row.method.value):
        cash_weight = max(0.0, 1.0 - sum(policy.weights.values()))
        per_session = [
            sum(policy.weights[key] * row.net_returns[key] for key in ordered_candidate_ids)
            + cash_weight * row.cash_net_return
            for row in sessions
        ]
        equity = 1.0
        peak = 1.0
        max_drawdown = 0.0
        for value in per_session:
            if value <= -1:
                raise AllocationBenchmarkError("portfolio session loss cannot exhaust capital")
            equity *= 1.0 + value
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
        volatility = stdev(per_session) if len(per_session) > 1 else 0.0
        excess = [value - row.cash_net_return for value, row in zip(per_session, sessions, strict=True)]
        excess_mean = sum(excess) / len(excess)
        metrics[policy.method.value] = MappingProxyType({
            "policy_version": policy.policy_version,
            "policy_sha256": policy.digest(),
            "weights": dict(policy.weights),
            "cash_weight": cash_weight,
            "total_net_return": equity - 1.0,
            "annualized_volatility": volatility * math.sqrt(252.0),
            "maximum_drawdown": max_drawdown,
            "annualized_excess_over_cash": excess_mean * 252.0,
            "sharpe_over_cash": excess_mean / volatility * math.sqrt(252.0) if volatility > 0 else None,
            "session_return_sha256": _hash(per_session),
        })
    report_body = {"evaluation_data_sha256": evaluation_data_sha256,
                   "session_ids": session_ids,
                   "methods": {key: dict(value) for key, value in sorted(metrics.items())},
                   "claim_scope": "SAME_COHORT_PAPER_RESEARCH_ONLY",
                   "promotion_allowed": False}
    return AllocationBenchmarkReport(evaluation_data_sha256, len(sessions),
                                    MappingProxyType(dict(sorted(metrics.items()))),
                                    _hash(report_body))


__all__ = ["AllocationBenchmarkError", "AllocationBenchmarkReport", "AllocationEvaluationSession",
           "AllocationMethod", "FrozenAllocationPolicy", "compare_allocation_methods"]
