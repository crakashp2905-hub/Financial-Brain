"""Strategy-independent immutable experiment registration contract."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any


class ExperimentSpecError(ValueError):
    """Incomplete or internally inconsistent pre-result experiment specification."""


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    experiment_id: str
    hypothesis: str
    economic_mechanism: str
    universe_definition: str
    decision_clock: str
    parameter_bounds: tuple[tuple[str, float, float], ...]
    evaluation_start: date
    evaluation_end: date
    benchmark: str
    failure_criteria: tuple[str, ...]
    spec_version: str = "1"

    def __post_init__(self) -> None:
        required = (self.experiment_id, self.hypothesis, self.economic_mechanism,
                    self.universe_definition, self.decision_clock, self.benchmark, self.spec_version)
        if any(not isinstance(value, str) or not value.strip() for value in required):
            raise ExperimentSpecError("experiment identity, hypothesis, mechanism, universe, clock, benchmark and version are required")
        if self.evaluation_start >= self.evaluation_end:
            raise ExperimentSpecError("evaluation window must have positive duration")
        if not self.parameter_bounds:
            raise ExperimentSpecError("at least one explicit parameter bound is required")
        names = [name for name, _, _ in self.parameter_bounds]
        if len(names) != len(set(names)):
            raise ExperimentSpecError("parameter names must be unique")
        for name, lower, upper in self.parameter_bounds:
            if not name.strip() or not math.isfinite(lower) or not math.isfinite(upper) or lower > upper:
                raise ExperimentSpecError("parameter bounds must have named finite ordered endpoints")
        if not self.failure_criteria or any(not isinstance(value, str) or not value.strip()
                                            for value in self.failure_criteria):
            raise ExperimentSpecError("explicit failure criteria are required")

    def body(self) -> dict[str, Any]:
        value = asdict(self)
        value["evaluation_start"] = self.evaluation_start.isoformat()
        value["evaluation_end"] = self.evaluation_end.isoformat()
        value["parameter_bounds"] = [list(row) for row in self.parameter_bounds]
        value["failure_criteria"] = list(self.failure_criteria)
        return {"schema": "fb.experiment_spec.v1", **value}

    def sha256(self) -> str:
        canonical = json.dumps(self.body(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode()).hexdigest()


__all__ = ["ExperimentSpec", "ExperimentSpecError"]
