"""Prospective champion selection separated from final confirmation outcomes."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Mapping, Sequence


class ProspectiveCohortError(ValueError):
    """A champion selection or its confirmation cohort is not valid."""


def _dates(values: Sequence[str], label: str) -> tuple[str, ...]:
    if not values:
        raise ProspectiveCohortError(f"{label} must not be empty")
    try:
        parsed = tuple(date.fromisoformat(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise ProspectiveCohortError(f"{label} must contain ISO session dates") from exc
    canonical = tuple(item.isoformat() for item in parsed)
    if canonical != tuple(values) or tuple(sorted(set(canonical))) != canonical:
        raise ProspectiveCohortError(f"{label} must be unique, canonical and chronological")
    return canonical


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProspectiveCohortError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class FrozenChampionPlan:
    """Content-addressed decision frozen before the first confirmation observation."""

    champion_id: str
    development_sessions: tuple[str, ...]
    confirmation_sessions: tuple[str, ...]
    development_returns_sha256: str
    frozen_at_utc: str
    first_confirmation_observation_at_utc: str
    plan_sha256: str


@dataclass(frozen=True)
class ConfirmationResult:
    champion_id: str
    scheduled_sessions: int
    observed_sessions: int
    mean_net_return: float | None
    missing_sessions: tuple[str, ...]
    plan_sha256: str


def freeze_champion(
    *,
    development_returns: Mapping[str, Sequence[float]],
    development_sessions: Sequence[str],
    confirmation_sessions: Sequence[str],
    frozen_at: datetime,
    first_confirmation_observation_at: datetime,
) -> FrozenChampionPlan:
    """Select using development returns only, then freeze a disjoint confirmation plan.

    Confirmation outcomes are deliberately not accepted by this API, preventing the
    selector from seeing final-cohort returns. Ties are resolved lexically.
    """
    development = _dates(development_sessions, "development_sessions")
    confirmation = _dates(confirmation_sessions, "confirmation_sessions")
    if set(development) & set(confirmation):
        raise ProspectiveCohortError("development and confirmation sessions must not overlap")
    if confirmation[0] <= development[-1]:
        raise ProspectiveCohortError("confirmation sessions must follow development sessions")
    if not development_returns:
        raise ProspectiveCohortError("development return candidates are required")
    normalized: dict[str, tuple[float, ...]] = {}
    for candidate_id, values in development_returns.items():
        if not candidate_id or len(values) != len(development):
            raise ProspectiveCohortError("each candidate needs one return per development session")
        returns = tuple(float(value) for value in values)
        if not all(math.isfinite(value) for value in returns):
            raise ProspectiveCohortError("development returns must be finite")
        normalized[candidate_id] = returns
    # Stable ordering makes equal means deterministic and reproducible.
    champion = min(normalized, key=lambda key: (-sum(normalized[key]) / len(development), key))
    frozen = _utc(frozen_at, "frozen_at")
    first = _utc(first_confirmation_observation_at, "first_confirmation_observation_at")
    if frozen >= first:
        raise ProspectiveCohortError("champion must be frozen before confirmation is observed")
    dev_payload = {
        "sessions": development,
        "returns": {key: list(normalized[key]) for key in sorted(normalized)},
    }
    dev_hash = hashlib.sha256(json.dumps(dev_payload, sort_keys=True, separators=(",", ":"),
                                         allow_nan=False).encode()).hexdigest()
    payload = {
        "champion_id": champion,
        "development_sessions": development,
        "confirmation_sessions": confirmation,
        "development_returns_sha256": dev_hash,
        "frozen_at_utc": frozen.isoformat(),
        "first_confirmation_observation_at_utc": first.isoformat(),
    }
    plan_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                          allow_nan=False).encode()).hexdigest()
    return FrozenChampionPlan(champion, development, confirmation, dev_hash, frozen.isoformat(),
                              first.isoformat(), plan_hash)


def confirm_champion(
    plan: FrozenChampionPlan,
    *,
    confirmation_returns: Mapping[str, float],
) -> ConfirmationResult:
    """Score the frozen champion on every scheduled session; missing dates stay missing."""
    scheduled = _dates(plan.confirmation_sessions, "confirmation_sessions")
    unexpected = set(confirmation_returns) - set(scheduled)
    if unexpected:
        raise ProspectiveCohortError("confirmation returns contain unscheduled dates")
    observed: list[float] = []
    for value in confirmation_returns.values():
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ProspectiveCohortError("confirmation returns must be finite")
        observed.append(numeric)
    missing = tuple(session for session in scheduled if session not in confirmation_returns)
    mean = sum(observed) / len(observed) if observed and not missing else None
    return ConfirmationResult(plan.champion_id, len(scheduled), len(observed), mean, missing,
                              plan.plan_sha256)
