"""Immutable provenance contract for a net-alpha hurdle selected before validation."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from math import isfinite


class ForecastHurdleError(ValueError):
    """A hurdle is malformed, out of period, or differs from the frozen strategy pin."""


@dataclass(frozen=True)
class FrozenForecastHurdle:
    policy_id: str
    min_net_alpha: float
    training_start: date
    training_end: date
    validation_start: date
    training_data_sha256: str
    selection_method: str

    def __post_init__(self) -> None:
        if not self.policy_id.strip() or not self.selection_method.strip():
            raise ForecastHurdleError("hurdle policy id and selection method are required")
        if not isfinite(self.min_net_alpha) or self.min_net_alpha < 0:
            raise ForecastHurdleError("minimum net-alpha hurdle must be finite and non-negative")
        if not (self.training_start <= self.training_end < self.validation_start):
            raise ForecastHurdleError("training must end strictly before validation starts")
        if len(self.training_data_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.training_data_sha256):
            raise ForecastHurdleError("training data digest must be a lowercase sha256")

    def canonical(self) -> dict:
        data = asdict(self)
        for field in ("training_start", "training_end", "validation_start"):
            data[field] = data[field].isoformat()
        return data

    @property
    def sha256(self) -> str:
        payload = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def validate_for(self, *, as_of: date, expected_sha256: str) -> None:
        if as_of < self.validation_start:
            raise ForecastHurdleError("frozen hurdle cannot be used before validation starts")
        if expected_sha256 != self.sha256:
            raise ForecastHurdleError("hurdle differs from its frozen strategy pin")
