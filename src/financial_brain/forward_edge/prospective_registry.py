"""Durable, immutable registry for prospective champion confirmation cohorts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

from .durable import DurableJournal
from .prospective_cohort import FrozenChampionPlan


class ProspectiveRegistryError(ValueError):
    """A cohort plan or observed-session receipt conflicts with its frozen registry."""


def _digest(value: str, label: str) -> None:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise ProspectiveRegistryError(f"{label} must be a lowercase SHA-256 digest")


def _timestamp(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ProspectiveRegistryError(f"{label} must be a timezone-aware ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProspectiveRegistryError(f"{label} must be a timezone-aware ISO timestamp")
    return parsed.astimezone(timezone.utc)


def validate_plan(plan: FrozenChampionPlan) -> None:
    if not isinstance(plan, FrozenChampionPlan):
        raise ProspectiveRegistryError("a frozen champion plan is required")
    try:
        development = tuple(date.fromisoformat(item).isoformat()
                           for item in plan.development_sessions)
        confirmation = tuple(date.fromisoformat(item).isoformat()
                             for item in plan.confirmation_sessions)
    except (TypeError, ValueError) as exc:
        raise ProspectiveRegistryError("plan sessions must be canonical ISO dates") from exc
    if (development != plan.development_sessions or confirmation != plan.confirmation_sessions
            or not development or not confirmation
            or tuple(sorted(set(development))) != development
            or tuple(sorted(set(confirmation))) != confirmation
            or set(development) & set(confirmation)
            or confirmation[0] <= development[-1]):
        raise ProspectiveRegistryError("cohort schedules must be ordered, disjoint, and chronological")
    if not isinstance(plan.champion_id, str) or not plan.champion_id.strip():
        raise ProspectiveRegistryError("champion identity is required")
    _digest(plan.development_returns_sha256, "development return digest")
    _digest(plan.plan_sha256, "cohort plan digest")
    frozen = _timestamp(plan.frozen_at_utc, "cohort freeze time")
    first = _timestamp(plan.first_confirmation_observation_at_utc,
                       "first observation time")
    if frozen >= first:
        raise ProspectiveRegistryError("cohort plan must be frozen before first observation")
    body = {
        "champion_id": plan.champion_id,
        "development_sessions": development,
        "confirmation_sessions": confirmation,
        "development_returns_sha256": plan.development_returns_sha256,
        "frozen_at_utc": plan.frozen_at_utc,
        "first_confirmation_observation_at_utc": plan.first_confirmation_observation_at_utc,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != plan.plan_sha256:
        raise ProspectiveRegistryError("cohort plan digest does not match its contents")


def _plan_from_dict(value: dict) -> FrozenChampionPlan:
    expected = {
        "champion_id", "development_sessions", "confirmation_sessions",
        "development_returns_sha256", "frozen_at_utc",
        "first_confirmation_observation_at_utc", "plan_sha256",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ProspectiveRegistryError("malformed frozen cohort plan")
    try:
        plan = FrozenChampionPlan(
            champion_id=value["champion_id"],
            development_sessions=tuple(value["development_sessions"]),
            confirmation_sessions=tuple(value["confirmation_sessions"]),
            development_returns_sha256=value["development_returns_sha256"],
            frozen_at_utc=value["frozen_at_utc"],
            first_confirmation_observation_at_utc=value["first_confirmation_observation_at_utc"],
            plan_sha256=value["plan_sha256"],
        )
    except (TypeError, KeyError) as exc:
        raise ProspectiveRegistryError("malformed frozen cohort plan") from exc
    validate_plan(plan)
    return plan


class ProspectiveCohortRegistry:
    """Append-only plan and outcome-digest registry; timestamps remain caller evidence."""

    def __init__(self, path: Path) -> None:
        self.journal = DurableJournal(Path(path))
        self._plans: dict[str, FrozenChampionPlan] = {}
        self._observations: dict[tuple[str, str], dict] = {}
        self._rebuild()

    @staticmethod
    def _cohort_id(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ProspectiveRegistryError("cohort ID is required")
        return value.strip()

    def _accept_plan(self, cohort_id: str, plan: FrozenChampionPlan) -> None:
        validate_plan(plan)
        prior = self._plans.get(cohort_id)
        if prior is not None and prior != plan:
            raise ProspectiveRegistryError("registered cohort plan is immutable")
        self._plans[cohort_id] = plan

    def _normalize_observation(self, row: dict) -> dict:
        try:
            cohort_id = self._cohort_id(row["cohort_id"])
            session = row["session"]
            observed_at = row["observed_at_utc"]
            outcome_sha256 = row["outcome_sha256"]
            plan = self._plans[cohort_id]
        except KeyError as exc:
            raise ProspectiveRegistryError("observation references an unknown cohort") from exc
        try:
            canonical_session = date.fromisoformat(session).isoformat()
        except (TypeError, ValueError) as exc:
            raise ProspectiveRegistryError("observation session must be a canonical ISO date") from exc
        if canonical_session != session or session not in plan.confirmation_sessions:
            raise ProspectiveRegistryError("observation session is outside the frozen schedule")
        if _timestamp(observed_at, "observation time") < _timestamp(
                plan.first_confirmation_observation_at_utc, "first observation time"):
            raise ProspectiveRegistryError("observation predates the frozen cohort start")
        _digest(outcome_sha256, "outcome digest")
        key = (cohort_id, session)
        normalized = {"cohort_id": cohort_id, "session": session,
                      "observed_at_utc": _timestamp(observed_at, "observation time").isoformat(),
                      "outcome_sha256": outcome_sha256}
        prior = self._observations.get(key)
        if prior is not None and prior != normalized:
            raise ProspectiveRegistryError("cohort session observation is immutable")
        return normalized

    def _accept_observation(self, row: dict) -> None:
        normalized = self._normalize_observation(row)
        self._observations[(normalized["cohort_id"], normalized["session"])] = normalized

    def _rebuild(self) -> None:
        try:
            for row in self.journal.records():
                event = row.get("t")
                if event == "COHORT_PLAN_REGISTERED":
                    cohort_id = self._cohort_id(row["cohort_id"])
                    plan = _plan_from_dict(row["plan"])
                    if row.get("plan_sha256") != plan.plan_sha256:
                        raise ProspectiveRegistryError("registered plan digest mismatch")
                    self._accept_plan(cohort_id, plan)
                elif event == "COHORT_OBSERVATION_RECORDED":
                    self._accept_observation(row)
                else:
                    raise ProspectiveRegistryError("unknown prospective cohort registry event")
        except (KeyError, TypeError) as exc:
            raise ProspectiveRegistryError("malformed prospective cohort registry event") from exc

    def register(self, cohort_id: str, plan: FrozenChampionPlan) -> str:
        cohort_id = self._cohort_id(cohort_id)
        validate_plan(plan)
        prior = self._plans.get(cohort_id)
        if prior is not None:
            if prior != plan:
                raise ProspectiveRegistryError("registered cohort plan is immutable")
            return plan.plan_sha256
        self.journal.append({"t": "COHORT_PLAN_REGISTERED", "cohort_id": cohort_id,
                             "plan": asdict(plan), "plan_sha256": plan.plan_sha256})
        self._plans[cohort_id] = plan
        return plan.plan_sha256

    def resolve(self, cohort_id: str) -> FrozenChampionPlan:
        cohort_id = self._cohort_id(cohort_id)
        try:
            plan = self._plans[cohort_id]
        except KeyError as exc:
            raise ProspectiveRegistryError("unknown prospective cohort") from exc
        validate_plan(plan)
        return plan

    def record_observation(self, *, cohort_id: str, session: str,
                           observed_at: datetime, outcome_sha256: str) -> dict:
        cohort_id = self._cohort_id(cohort_id)
        if not isinstance(session, str):
            raise ProspectiveRegistryError("observation session must be an ISO date")
        if not isinstance(observed_at, datetime) or observed_at.tzinfo is None \
                or observed_at.utcoffset() is None:
            raise ProspectiveRegistryError("observation time must be timezone-aware")
        row = {"cohort_id": cohort_id, "session": session,
               "observed_at_utc": observed_at.astimezone(timezone.utc).isoformat(),
               "outcome_sha256": outcome_sha256}
        normalized = self._normalize_observation(row)
        key = (cohort_id, session)
        prior = self._observations.get(key)
        if prior is None:
            self.journal.append({"t": "COHORT_OBSERVATION_RECORDED", **normalized})
            self._observations[key] = normalized
        return dict(self._observations[key])

    def observations(self, cohort_id: str) -> tuple[dict, ...]:
        cohort_id = self._cohort_id(cohort_id)
        self.resolve(cohort_id)
        return tuple(dict(self._observations[key]) for key in sorted(self._observations)
                     if key[0] == cohort_id)
