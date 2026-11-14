"""Prospective sample-size design from benefit, volatility, dependence and power."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from statistics import NormalDist
import hashlib
import json
import math


class SampleSizeError(ValueError):
    """Invalid or post-observation sample-size design."""


def _utc(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise SampleSizeError(f"{label} must be a timezone-aware ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SampleSizeError(f"{label} must be a timezone-aware ISO timestamp")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class SampleSizePlan:
    design_id: str
    detectable_daily_benefit: float
    daily_volatility: float
    target_power: float
    familywise_alpha: float
    planned_looks: int
    dependence_design_effect: float
    expected_observation_rate: float
    session_floor: int
    iid_required_sessions: int
    dependence_adjusted_sessions: int
    required_sessions: int
    power_at_floor: float
    frozen_at_utc: str
    first_observation_at_utc: str

    def sha256(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"),
                             allow_nan=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def design_sample_size(
    *,
    design_id: str,
    detectable_daily_benefit: float,
    daily_volatility: float,
    target_power: float,
    familywise_alpha: float,
    planned_looks: int,
    dependence_design_effect: float,
    frozen_at_utc: str,
    first_observation_at_utc: str,
    expected_observation_rate: float = 1.0,
    session_floor: int = 60,
) -> SampleSizePlan:
    """Return a one-sided normal-approximation design, preserving a minimum session floor."""
    if not design_id.strip():
        raise SampleSizeError("design id is required")
    numeric = (detectable_daily_benefit, daily_volatility, target_power, familywise_alpha,
               dependence_design_effect, expected_observation_rate)
    if not all(math.isfinite(value) for value in numeric):
        raise SampleSizeError("design parameters must be finite")
    if detectable_daily_benefit <= 0 or daily_volatility <= 0:
        raise SampleSizeError("detectable benefit and volatility must be positive")
    if not 0.5 < target_power < 1:
        raise SampleSizeError("target power must be between 0.5 and 1")
    if not 0 < familywise_alpha < 0.5:
        raise SampleSizeError("familywise alpha must be between zero and 0.5")
    if type(planned_looks) is not int or planned_looks < 1:
        raise SampleSizeError("planned looks must be a positive integer")
    if dependence_design_effect < 1:
        raise SampleSizeError("dependence design effect must be at least one")
    if not 0 < expected_observation_rate <= 1:
        raise SampleSizeError("expected observation rate must be in (0, 1]")
    if type(session_floor) is not int or session_floor < 1:
        raise SampleSizeError("session floor must be a positive integer")
    frozen = _utc(frozen_at_utc, "frozen_at_utc")
    first = _utc(first_observation_at_utc, "first_observation_at_utc")
    if frozen >= first:
        raise SampleSizeError("sample-size design must be frozen before the first observation")

    per_look_alpha = familywise_alpha / planned_looks
    z_alpha = NormalDist().inv_cdf(1 - per_look_alpha)
    z_power = NormalDist().inv_cdf(target_power)
    ratio = daily_volatility / detectable_daily_benefit
    iid_required = math.ceil(((z_alpha + z_power) * ratio) ** 2)
    adjusted = math.ceil(iid_required * dependence_design_effect)
    required = max(session_floor, math.ceil(adjusted / expected_observation_rate))
    effective_at_floor = session_floor * expected_observation_rate / dependence_design_effect
    power_at_floor = NormalDist().cdf(
        math.sqrt(effective_at_floor) / ratio - z_alpha
    )
    return SampleSizePlan(
        design_id=design_id,
        detectable_daily_benefit=detectable_daily_benefit,
        daily_volatility=daily_volatility,
        target_power=target_power,
        familywise_alpha=familywise_alpha,
        planned_looks=planned_looks,
        dependence_design_effect=dependence_design_effect,
        expected_observation_rate=expected_observation_rate,
        session_floor=session_floor,
        iid_required_sessions=iid_required,
        dependence_adjusted_sessions=adjusted,
        required_sessions=required,
        power_at_floor=power_at_floor,
        frozen_at_utc=frozen.isoformat(),
        first_observation_at_utc=first.isoformat(),
    )
