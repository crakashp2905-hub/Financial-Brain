"""Append-only, human-reviewed log for factual data-corruption exclusions.

An excluded session stays in the scheduled denominator. The sensitivity report shows both the observed-only diagnostic
and a fixed-denominator adverse scenario assigning the configured loss floor to each excluded or unresolved date.
This module cannot establish reviewer identity outside the supplied approval record.
"""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from financial_brain.forward_edge.approvals import Approval, ApprovalError, is_agent_actor, validate_approval
from financial_brain.forward_edge.durable import DurableJournal

EXCLUSION_REASONS = frozenset({"SOURCE_CORRUPTION", "TIMESTAMP_CORRUPTION", "SCHEMA_CORRUPTION"})


class ExclusionError(ValueError):
    pass


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def _sha256(value: str, label: str) -> None:
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ExclusionError(f"{label} must be a lowercase SHA-256 digest")


@dataclass(frozen=True)
class ExclusionRequest:
    session_id: str
    session_date: str
    reason_code: str
    evidence_sha256: str
    rationale: str
    reported_by: str

    def validate(self) -> None:
        if not all(isinstance(x, str) and x.strip() == x and x for x in
                   (self.session_id, self.session_date, self.reason_code, self.rationale, self.reported_by)):
            raise ExclusionError("session, date, reason, rationale and reporter are required")
        try:
            if date.fromisoformat(self.session_date).isoformat() != self.session_date:
                raise ValueError
        except ValueError as exc:
            raise ExclusionError("session_date must be a canonical ISO date") from exc
        if self.reason_code not in EXCLUSION_REASONS:
            raise ExclusionError("exclusions are limited to factual source, timestamp or schema corruption")
        _sha256(self.evidence_sha256, "evidence_sha256")
        if is_agent_actor(self.reported_by, "human"):
            raise ExclusionError("automated actors cannot request data exclusions")

    def as_dict(self) -> dict[str, str]:
        self.validate()
        return asdict(self)

    def sha256(self) -> str:
        return hashlib.sha256(_canonical(self.as_dict())).hexdigest()


def _approval_dict(approval: Approval) -> dict[str, str | None]:
    return {
        "kind": approval.kind,
        "subject": approval.subject,
        "scope_sha256": approval.scope_sha256,
        "actor_id": approval.actor_id,
        "actor_type": approval.actor_type,
        "approved_at_utc": approval.approved_at_utc.astimezone(timezone.utc).isoformat(),
        "expires_at_utc": approval.expires_at_utc.astimezone(timezone.utc).isoformat(),
        "adr_ref": approval.adr_ref,
    }


def _approval_from_dict(value: dict) -> Approval:
    expected = {"kind", "subject", "scope_sha256", "actor_id", "actor_type", "approved_at_utc",
                "expires_at_utc", "adr_ref"}
    if not isinstance(value, dict) or set(value) != expected:
        raise ExclusionError("malformed exclusion approval record")
    try:
        return Approval(
            kind=value["kind"], subject=value["subject"], scope_sha256=value["scope_sha256"],
            actor_id=value["actor_id"], actor_type=value["actor_type"],
            approved_at_utc=datetime.fromisoformat(value["approved_at_utc"]),
            expires_at_utc=datetime.fromisoformat(value["expires_at_utc"]), adr_ref=value["adr_ref"],
        )
    except (TypeError, ValueError) as exc:
        raise ExclusionError("malformed exclusion approval timestamps") from exc


class ExclusionRegistry:
    """Durable exclusion registry requiring a second, distinct human reviewer per factual exclusion."""

    def __init__(self, path: Path) -> None:
        self.journal = DurableJournal(Path(path))
        self._entries: dict[tuple[str, str], dict] = {}
        self._rebuild()

    def _accept(self, request: ExclusionRequest, approval: Approval, recorded_at: datetime) -> dict:
        request.validate()
        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            raise ExclusionError("recorded_at must be timezone-aware")
        if approval.actor_id == request.reported_by:
            raise ExclusionError("exclusion reviewer must be a different human from the reporter")
        try:
            validate_approval(approval, kind="DATA_EXCLUSION_REVIEW", subject=request.session_id,
                              scope_sha256=request.sha256(), now=recorded_at)
        except ApprovalError as exc:
            raise ExclusionError(f"independent review rejected: {exc}") from exc
        return {
            "request": request.as_dict(),
            "request_sha256": request.sha256(),
            "review": _approval_dict(approval),
            "recorded_at_utc": recorded_at.astimezone(timezone.utc).isoformat(),
        }

    def _rebuild(self) -> None:
        for row in self.journal.records():
            if not isinstance(row, dict) or row.get("t") != "DATA_EXCLUSION_REVIEWED":
                raise ExclusionError("unknown exclusion registry event")
            try:
                request = ExclusionRequest(**row["request"])
                approval = _approval_from_dict(row["review"])
                recorded_at = datetime.fromisoformat(row["recorded_at_utc"])
                entry = self._accept(request, approval, recorded_at)
            except (KeyError, TypeError, ValueError) as exc:
                if isinstance(exc, ExclusionError):
                    raise
                raise ExclusionError("malformed exclusion registry event") from exc
            if row.get("request_sha256") != entry["request_sha256"]:
                raise ExclusionError("exclusion request digest mismatch")
            key = (request.session_id, request.session_date)
            previous = self._entries.get(key)
            if previous is not None and previous != entry:
                raise ExclusionError("a reviewed session exclusion is immutable")
            self._entries[key] = entry

    def record(self, request: ExclusionRequest, approval: Approval, *, recorded_at: datetime) -> str:
        entry = self._accept(request, approval, recorded_at)
        key = (request.session_id, request.session_date)
        previous = self._entries.get(key)
        if previous is not None:
            if previous != entry:
                raise ExclusionError("a reviewed session exclusion is immutable")
            return entry["request_sha256"]
        self.journal.append({"t": "DATA_EXCLUSION_REVIEWED", **entry})
        self._entries[key] = entry
        return entry["request_sha256"]

    def entries(self) -> list[dict]:
        return [deepcopy(self._entries[key]) for key in sorted(self._entries)]


def sensitivity_report(*, scheduled_sessions: list[str], daily_net_returns: dict[str, float],
                        exclusions: ExclusionRegistry, excluded_return_floor: float = -1.0) -> dict:
    """Keep every scheduled date in the denominator and expose excluded/missing-day adverse sensitivity."""
    if not scheduled_sessions or scheduled_sessions != sorted(set(scheduled_sessions)):
        raise ExclusionError("scheduled sessions must be non-empty, unique and ordered")
    try:
        if any(date.fromisoformat(day).isoformat() != day for day in scheduled_sessions):
            raise ValueError
    except ValueError as exc:
        raise ExclusionError("scheduled sessions must be canonical ISO dates") from exc
    if not math.isfinite(excluded_return_floor):
        raise ExclusionError("excluded return floor must be finite")
    if any(day not in scheduled_sessions or not math.isfinite(float(value))
           for day, value in daily_net_returns.items()):
        raise ExclusionError("returns must be finite and belong to the frozen session denominator")
    session_set = set(scheduled_sessions)
    excluded = [row for row in exclusions.entries()
                if row["request"]["session_date"] in session_set]
    excluded_by_day = {row["request"]["session_date"]: row for row in excluded}
    if len(excluded_by_day) != len(excluded):
        raise ExclusionError("only one approved exclusion is allowed per scheduled session")
    overlap = set(daily_net_returns).intersection(excluded_by_day)
    if overlap:
        raise ExclusionError(f"session cannot be both observed and excluded: {sorted(overlap)}")
    missing = sorted(session_set - set(daily_net_returns) - set(excluded_by_day))
    denominator = len(scheduled_sessions)
    observed_sum = sum(float(value) for value in daily_net_returns.values())
    adverse_missing = len(excluded) + len(missing)
    return {
        "status": "COMPLETE" if not missing and not excluded else "REVIEW_REQUIRED",
        "scheduled_denominator": denominator,
        "observed_sessions": len(daily_net_returns),
        "approved_exclusions": len(excluded),
        "unreviewed_missing_sessions": len(missing),
        "scheduled_dates": list(scheduled_sessions),
        "excluded_dates": [row["request"]["session_date"] for row in excluded],
        "unreviewed_missing_dates": missing,
        "observed_only_mean_diagnostic": observed_sum / len(daily_net_returns) if daily_net_returns else None,
        "fixed_denominator_zero_fill_sensitivity": observed_sum / denominator,
        "fixed_denominator_adverse_sensitivity": (observed_sum + adverse_missing * excluded_return_floor) / denominator,
        "adverse_sensitivity_floor_per_missing_session": excluded_return_floor,
        "promotion_eligible": not missing and not excluded,
    }
