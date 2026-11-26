"""Fail-closed access policy for research and outcome-evaluation tiers.

This module is an authorization boundary for callers to consult before loading data. It does
not itself provide storage isolation; sealed datasets must be placed behind an adapter that
calls ``authorize`` before exposing any rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EvaluationTier(StrEnum):
    EXPLORATORY_SCREENING = "EXPLORATORY_SCREENING"
    PREREGISTERED_VALIDATION = "PREREGISTERED_VALIDATION"
    SINGLE_USE_HOLDOUT = "SINGLE_USE_HOLDOUT"
    FUTURE_OBSERVATION = "FUTURE_OBSERVATION"


class ResearchAction(StrEnum):
    INSPECT = "INSPECT"
    TUNE = "TUNE"
    VALIDATE_FROZEN = "VALIDATE_FROZEN"
    OPEN_HOLDOUT = "OPEN_HOLDOUT"
    SCORE_OBSERVATION = "SCORE_OBSERVATION"
    PROMOTE = "PROMOTE"


class TierAccessError(PermissionError):
    """The requested operation is not permitted for the data tier."""


@dataclass(frozen=True)
class TierRequest:
    tier: EvaluationTier
    action: ResearchAction
    run_id: str
    preregistration_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tier, EvaluationTier) or not isinstance(self.action, ResearchAction):
            raise TierAccessError("tier and action must use the closed policy enums")
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise TierAccessError("a stable run ID is required")
        if self.preregistration_sha256 is not None and (
            len(self.preregistration_sha256) != 64
            or any(c not in "0123456789abcdef" for c in self.preregistration_sha256)
        ):
            raise TierAccessError("preregistration identity must be a lowercase SHA-256")


_ALLOWED: dict[EvaluationTier, frozenset[ResearchAction]] = {
    EvaluationTier.EXPLORATORY_SCREENING: frozenset({ResearchAction.INSPECT, ResearchAction.TUNE}),
    EvaluationTier.PREREGISTERED_VALIDATION: frozenset({ResearchAction.VALIDATE_FROZEN}),
    EvaluationTier.SINGLE_USE_HOLDOUT: frozenset({ResearchAction.OPEN_HOLDOUT}),
    EvaluationTier.FUTURE_OBSERVATION: frozenset({ResearchAction.SCORE_OBSERVATION}),
}


def authorize(request: TierRequest) -> None:
    """Allow only the operations assigned to a tier; promotion is never granted here.

    A holdout request must bind to the frozen preregistration hash. Single-use consumption and
    independent owner authorization are enforced by the sealed-data service, not by this pure
    policy function.
    """
    if not isinstance(request, TierRequest):
        raise TierAccessError("a typed tier request is required")
    if request.action not in _ALLOWED[request.tier]:
        raise TierAccessError(f"{request.action.value} is forbidden for {request.tier.value}")
    if request.tier in {EvaluationTier.PREREGISTERED_VALIDATION, EvaluationTier.SINGLE_USE_HOLDOUT}:
        if request.preregistration_sha256 is None:
            raise TierAccessError("frozen validation requires a registered preregistration hash")


__all__ = ["EvaluationTier", "ResearchAction", "TierAccessError", "TierRequest", "authorize"]
