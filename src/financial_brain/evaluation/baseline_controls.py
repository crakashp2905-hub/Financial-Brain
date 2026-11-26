"""Frozen baseline-control contract for comparable strategy decisions.

This validates the design identity before results are compared. It does not manufacture missing
cash rates, index histories, exposure estimates, random portfolios, or transaction-cost evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import StrEnum


class BaselineControlError(ValueError):
    """Baseline set is incomplete or not comparable to the strategy evaluation."""


class BaselineKind(StrEnum):
    CASH = "CASH"
    MATCHED_INDEX_BUY_HOLD = "MATCHED_INDEX_BUY_HOLD"
    EQUAL_WEIGHT_UNIVERSE = "EQUAL_WEIGHT_UNIVERSE"
    EQUAL_TURNOVER_RANDOM = "EQUAL_TURNOVER_RANDOM"
    EXPOSURE_MATCHED = "EXPOSURE_MATCHED"
    COST_MATCHED_NULL = "COST_MATCHED_NULL"


REQUIRED_CONTROLS = frozenset(BaselineKind)


def _sha(value: str, label: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise BaselineControlError(f"{label} must be a lowercase SHA-256")


@dataclass(frozen=True)
class StrategyEvaluationIdentity:
    experiment_id: str
    universe_sha256: str
    calendar_sha256: str
    evaluation_window: tuple[str, str]
    cost_schedule_sha256: str
    exposure_profile_sha256: str
    turnover_fraction: float

    def __post_init__(self) -> None:
        if not self.experiment_id.strip():
            raise BaselineControlError("experiment ID is required")
        for value, label in ((self.universe_sha256, "universe"), (self.calendar_sha256, "calendar"),
                             (self.cost_schedule_sha256, "cost schedule"),
                             (self.exposure_profile_sha256, "exposure profile")):
            _sha(value, label)
        if len(self.evaluation_window) != 2 or not all(self.evaluation_window):
            raise BaselineControlError("evaluation window needs inclusive start/end labels")
        if self.evaluation_window[0] > self.evaluation_window[1]:
            raise BaselineControlError("evaluation window is reversed")
        if not math.isfinite(self.turnover_fraction) or self.turnover_fraction < 0:
            raise BaselineControlError("turnover fraction must be finite and nonnegative")


@dataclass(frozen=True)
class BaselineControl:
    kind: BaselineKind
    series_id: str
    universe_sha256: str
    calendar_sha256: str
    evaluation_window: tuple[str, str]
    cost_schedule_sha256: str
    exposure_profile_sha256: str
    turnover_fraction: float
    construction_sha256: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, BaselineKind) or not self.series_id.strip():
            raise BaselineControlError("typed baseline kind and series ID are required")
        for value, label in ((self.universe_sha256, "universe"), (self.calendar_sha256, "calendar"),
                             (self.cost_schedule_sha256, "cost schedule"),
                             (self.exposure_profile_sha256, "exposure profile"),
                             (self.construction_sha256, "construction"), (self.evidence_sha256, "evidence")):
            _sha(value, label)
        if not math.isfinite(self.turnover_fraction) or self.turnover_fraction < 0:
            raise BaselineControlError("turnover fraction must be finite and nonnegative")


@dataclass(frozen=True)
class FrozenBaselineSet:
    experiment: StrategyEvaluationIdentity
    controls: tuple[BaselineControl, ...]

    def __post_init__(self) -> None:
        kinds = [c.kind for c in self.controls]
        if len(kinds) != len(set(kinds)) or set(kinds) != REQUIRED_CONTROLS:
            missing = sorted(k.value for k in REQUIRED_CONTROLS - set(kinds))
            extra = sorted(k.value for k in set(kinds) - REQUIRED_CONTROLS)
            raise BaselineControlError(f"baseline controls must be exactly the six required kinds; missing={missing}, extra={extra}")
        for control in self.controls:
            if control.universe_sha256 != self.experiment.universe_sha256:
                raise BaselineControlError(f"{control.kind.value} uses a different universe")
            if control.calendar_sha256 != self.experiment.calendar_sha256:
                raise BaselineControlError(f"{control.kind.value} uses a different calendar")
            if control.evaluation_window != self.experiment.evaluation_window:
                raise BaselineControlError(f"{control.kind.value} uses a different evaluation window")
            if control.cost_schedule_sha256 != self.experiment.cost_schedule_sha256:
                raise BaselineControlError(f"{control.kind.value} uses a different cost schedule identity")
        by_kind = {c.kind: c for c in self.controls}
        if by_kind[BaselineKind.EXPOSURE_MATCHED].exposure_profile_sha256 != self.experiment.exposure_profile_sha256:
            raise BaselineControlError("exposure-matched baseline does not match strategy exposures")
        if not math.isclose(by_kind[BaselineKind.EQUAL_TURNOVER_RANDOM].turnover_fraction,
                            self.experiment.turnover_fraction, rel_tol=0.0, abs_tol=1e-9):
            raise BaselineControlError("random baseline turnover does not match strategy turnover")

    def sha256(self) -> str:
        rows = [{"kind": c.kind.value, "series_id": c.series_id,
                 "universe_sha256": c.universe_sha256, "calendar_sha256": c.calendar_sha256,
                 "evaluation_window": list(c.evaluation_window), "cost_schedule_sha256": c.cost_schedule_sha256,
                 "exposure_profile_sha256": c.exposure_profile_sha256,
                 "turnover_fraction": c.turnover_fraction, "construction_sha256": c.construction_sha256,
                 "evidence_sha256": c.evidence_sha256} for c in sorted(self.controls, key=lambda x: x.kind.value)]
        payload = {"experiment_id": self.experiment.experiment_id, "controls": rows}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()


__all__ = ["BaselineControl", "BaselineControlError", "BaselineKind", "FrozenBaselineSet",
           "StrategyEvaluationIdentity"]
