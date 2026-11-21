"""Source-backed lifecycle facts for a survivorship-aware MF scheme universe."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from ..forward_edge.data_permissions import DataPermission, DataUse
from ..kernel.trading_clock import IST


class SchemeLifecycleError(ValueError):
    """Invalid, unauthorized, or ambiguous mutual-fund lifecycle evidence."""


class SchemeLifecycle(StrEnum):
    ACTIVE = "ACTIVE"
    CLOSED_TO_SUBSCRIPTION = "CLOSED_TO_SUBSCRIPTION"
    CLOSED_TO_REDEMPTION = "CLOSED_TO_REDEMPTION"
    SUSPENDED = "SUSPENDED"
    MERGED = "MERGED"
    WOUND_UP = "WOUND_UP"
    RENAMED = "RENAMED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SchemeLifecycleRecord:
    """One effective-dated lifecycle assertion for a canonical scheme identity."""

    scheme_code: str
    plan: str
    option: str
    isin: str
    lifecycle: SchemeLifecycle
    effective_from: date
    effective_to: date | None
    successor_scheme_code: str | None
    source_id: str
    source_ref: str
    source_sha256: str
    published_at: datetime
    observed_at: datetime

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "lifecycle", SchemeLifecycle(self.lifecycle))
        except ValueError as exc:
            raise SchemeLifecycleError("invalid lifecycle state") from exc
        for name in ("scheme_code", "plan", "option", "isin", "source_id", "source_ref"):
            if not getattr(self, name).strip():
                raise SchemeLifecycleError(f"{name} is required")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise SchemeLifecycleError("effective_to precedes effective_from")
        if self.lifecycle in (SchemeLifecycle.MERGED, SchemeLifecycle.RENAMED):
            if not self.successor_scheme_code or not self.successor_scheme_code.strip():
                raise SchemeLifecycleError("merged/renamed schemes require successor_scheme_code")
        if self.lifecycle not in (SchemeLifecycle.MERGED, SchemeLifecycle.RENAMED):
            if self.successor_scheme_code is not None:
                raise SchemeLifecycleError("successor is only valid for merge/rename lifecycle states")
        if len(self.source_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.source_sha256
        ):
            raise SchemeLifecycleError("source_sha256 must be lowercase SHA-256")
        for name in ("published_at", "observed_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise SchemeLifecycleError(f"{name} must be timezone-aware")

    def require_permissions(self, permissions: tuple[DataPermission, ...]) -> None:
        acquired_on = self.observed_at.astimezone(IST).date()
        for use in (DataUse.RESEARCH, DataUse.RETENTION):
            if not any(p.allows(source_id=self.source_id, use=use, on=acquired_on)
                       for p in permissions):
                raise SchemeLifecycleError(
                    f"source {self.source_id} lacks effective {use.value} permission"
                )

    @property
    def record_id(self) -> str:
        payload = {
            "scheme_code": self.scheme_code, "plan": self.plan, "option": self.option,
            "isin": self.isin, "lifecycle": self.lifecycle.value,
            "effective_from": self.effective_from.isoformat(),
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "successor_scheme_code": self.successor_scheme_code,
            "source_id": self.source_id, "source_ref": self.source_ref,
            "source_sha256": self.source_sha256,
            "published_at": self.published_at.isoformat(),
            "observed_at": self.observed_at.isoformat(),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


__all__ = ["SchemeLifecycle", "SchemeLifecycleError", "SchemeLifecycleRecord"]
