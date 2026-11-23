"""Purpose-scoped, effective-dated market-data permissions.

Presence of an API, a successful request, or a provider label does not grant a use.
Missing, stale, or out-of-scope permission objects fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
import hashlib
import json
from pathlib import Path
from typing import Any

DEFAULT_PERMISSION_REGISTRY = (Path(__file__).resolve().parents[3]
                               / "docs/edge-program/data-permissions.v1.json")


class PermissionStatus(StrEnum):
    USE_ALLOWED = "USE_ALLOWED"
    UNKNOWN_RIGHTS = "UNKNOWN_RIGHTS"
    DENIED = "DENIED"


class DataUse(StrEnum):
    RESEARCH = "RESEARCH"
    PAPER_SIMULATION = "PAPER_SIMULATION"
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


def load_permission_registry(path: Path | None = None
                             ) -> tuple[dict[tuple[str, DataUse], DataPermission], str]:
    """Load canonical source/use permissions and their LF-normalized evidence hash."""
    raw = Path(path or DEFAULT_PERMISSION_REGISTRY).read_bytes()
    normalized = raw.replace(b"\r\n", b"\n")
    document: Any = json.loads(normalized.decode("utf-8"))
    if not isinstance(document, dict) or document.get("schema") != "fb.data_permissions.v1":
        raise ValueError("data permission registry has an unsupported schema")
    rows = document.get("permissions")
    if not isinstance(rows, list) or not rows:
        raise ValueError("data permission registry is empty or malformed")
    registry: dict[tuple[str, DataUse], DataPermission] = {}
    fields = ("source_id", "use", "status", "effective_from", "effective_through",
              "evidence_ref", "reviewer")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("data permission registry row is not an object")
        permission = DataPermission.from_dict({key: row.get(key) for key in fields})
        key = (permission.source_id, permission.use)
        if key in registry:
            raise ValueError(f"duplicate source/use permission in registry: {key}")
        registry[key] = permission
    return registry, hashlib.sha256(normalized).hexdigest()
