"""Purpose-scoped, effective-dated market-data permissions.

Presence of an API, a successful request, or a provider label does not grant a use.
Missing, stale, or out-of-scope permission objects fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class PermissionStatus(StrEnum):
    USE_ALLOWED = "USE_ALLOWED"
    UNKNOWN_RIGHTS = "UNKNOWN_RIGHTS"
    DENIED = "DENIED"


class DataUse(StrEnum):
    RESEARCH = "RESEARCH"
    PAPER_SIMULATION = "PAPER_SIMULATION"
    PROSPECTIVE_PAPER_CAPTURE = "PROSPECTIVE_PAPER_CAPTURE"
    DISPLAY = "DISPLAY"
    RETENTION = "RETENTION"
    REDISTRIBUTION = "REDISTRIBUTION"
    LIVE_EXECUTION = "LIVE_EXECUTION"


@dataclass(frozen=True)
class DataPermission:
    source_id: str
    use: DataUse
    status: PermissionStatus
    effective_from: date | None
    effective_through: date | None
    evidence_ref: str
    reviewer: str | None

    def __post_init__(self) -> None:
        if not self.source_id.strip() or not self.evidence_ref.strip():
            raise ValueError("permission needs source and evidence reference")
        if self.status is PermissionStatus.USE_ALLOWED and (self.effective_from is None or
                                                             not self.reviewer or not self.reviewer.strip()):
            raise ValueError("USE_ALLOWED permission needs an effective date and reviewer")
        if self.status is PermissionStatus.UNKNOWN_RIGHTS and (self.effective_from is not None or
                                                               self.effective_through is not None):
            raise ValueError("unknown rights cannot assert an effective period")
        if self.effective_through is not None and (self.effective_from is None
                                                   or self.effective_through < self.effective_from):
            raise ValueError("permission end date precedes its effective date")
    def allows(self, *, source_id: str, use: DataUse, on: date) -> bool:
        return (self.status is PermissionStatus.USE_ALLOWED and self.effective_from is not None
                and self.source_id == source_id and self.use is use and self.effective_from <= on
                and (self.effective_through is None or on <= self.effective_through))

    @classmethod
    def from_dict(cls, row: dict) -> "DataPermission":
        expected = {"source_id", "use", "status", "effective_from", "effective_through",
                    "evidence_ref", "reviewer"}
        if set(row) != expected:
            raise ValueError("permission has missing or unknown fields")
        return cls(
            source_id=str(row["source_id"]), use=DataUse(row["use"]),
            status=PermissionStatus(row["status"]),
            effective_from=(date.fromisoformat(row["effective_from"]) if row["effective_from"] else None),
            effective_through=(date.fromisoformat(row["effective_through"])
                               if row["effective_through"] else None),
            evidence_ref=str(row["evidence_ref"]),
            reviewer=str(row["reviewer"]) if row["reviewer"] else None,
        )
