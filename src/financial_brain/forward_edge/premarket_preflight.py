"""Fail-closed, evidence-linked premarket checks for paper-session callbacks."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile


class PremarketError(ValueError):
    """Malformed premarket evidence or policy."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class PremarketPolicy:
    min_disk_free_bytes: int
    max_clock_skew_ms: float

    def __post_init__(self) -> None:
        if type(self.min_disk_free_bytes) is not int or self.min_disk_free_bytes < 0:
            raise PremarketError("minimum disk space must be a non-negative integer")
        if (type(self.max_clock_skew_ms) not in (int, float)
                or not math.isfinite(self.max_clock_skew_ms) or self.max_clock_skew_ms < 0):
            raise PremarketError("maximum clock skew must be finite and non-negative")

    @property
    def policy_sha256(self) -> str:
        body = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return hashlib.sha256(body).hexdigest()


@dataclass(frozen=True)
class PremarketSnapshot:
    session_id: str
    session_date: date
    checked_at: datetime
    calendar_is_session: bool | None
    calendar_version: str | None
    calendar_evidence_id: str | None
    data_entitlement_active: bool | None
    data_source: str | None
    entitlement_evidence_id: str | None
    broker_read_only_session: bool | None
    broker_session_id: str | None
    broker_evidence_id: str | None
    disk_free_bytes: int | None
    disk_probe_evidence_id: str | None
    archive_writable: bool | None
    archive_probe_evidence_id: str | None
    cost_schedule_sha256: str | None
    cost_effective_from: date | None
    cost_effective_until: date | None
    cost_evidence_id: str | None
    strategy_id: str | None
    strategy_version: str | None
    strategy_spec_sha256: str | None
    strategy_evidence_id: str | None
    risk_config_sha256: str | None
    risk_approved: bool | None
    risk_evidence_id: str | None
    clock_skew_ms: float | None
    clock_evidence_id: str | None


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    reason: str


@dataclass(frozen=True)
class PremarketReport:
    session_id: str
    session_date: date
    checked_at_utc: datetime
    policy_sha256: str
    checks: tuple[Check, ...]
    report_sha256: str

    @property
    def approved(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def status(self) -> str:
        return "READY" if self.approved else "NO_TRADE"

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(check.name for check in self.checks if not check.passed)

    @property
    def operator_reason(self) -> str:
        if self.approved:
            return "PREMARKET_PREFLIGHT_READY"
        details = "; ".join(check.reason for check in self.checks if not check.passed)
        return f"PREMARKET_NO_TRADE: {details}"

    def runner_result(self) -> dict:
        """Return the fail-closed dict consumed by ForwardSessionRunner.record_preflight."""
        return {
            "approved": self.approved,
            "status": self.status,
            "reason": self.operator_reason,
            "reason_codes": list(self.reason_codes),
            "session_id": self.session_id,
            "session_date": self.session_date.isoformat(),
            "checked_at_utc": self.checked_at_utc.isoformat(),
            "policy_sha256": self.policy_sha256,
            "report_sha256": self.report_sha256,
            "checks": [{"name": c.name, "passed": c.passed, "reason": c.reason} for c in self.checks],
        }


def probe_local_archive_storage(archive_directory: Path, *, probe_id: str) -> dict:
    """Probe local free space and write/fsync/delete access for an existing archive directory.

    The returned digest identifies this local probe result; it is not an external
    timestamp, entitlement receipt, or proof that the directory is production storage.
    """
    if not isinstance(probe_id, str) or not probe_id.strip():
        raise PremarketError("local storage probe ID is required")
    path = Path(archive_directory)
    checked_at = datetime.now(timezone.utc)
    resolved = None
    free_bytes = None
    writable = False
    reason = "archive directory is unavailable"
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_dir():
            raise NotADirectoryError(str(resolved))
        free_bytes = shutil.disk_usage(resolved).free
        descriptor, temporary_path = tempfile.mkstemp(prefix=".fb-preflight-", dir=resolved)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(b"financial-brain-premarket-probe\\n")
                stream.flush()
                os.fsync(stream.fileno())
            writable = True
            reason = "OK"
        finally:
            Path(temporary_path).unlink(missing_ok=True)
    except OSError as exc:
        reason = f"local archive probe failed: {type(exc).__name__}"
    evidence = {
        "probe_id": probe_id,
        "path": str(resolved) if resolved is not None else str(path),
        "checked_at_utc": checked_at.isoformat(),
        "disk_free_bytes": free_bytes,
        "archive_writable": writable,
        "reason": reason,
    }
    digest = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":"),
                                 allow_nan=False).encode()).hexdigest()
    return {
        "disk_free_bytes": free_bytes,
        "disk_probe_evidence_id": f"local-storage:{probe_id}:{digest}",
        "archive_writable": writable,
        "archive_probe_evidence_id": f"local-archive:{probe_id}:{digest}",
        "checked_at_utc": checked_at.isoformat(),
        "probe_sha256": digest,
        "reason": reason,
        "evidence_scope": "LOCAL_FILESYSTEM_PROBE_ONLY",
    }


def evaluate_premarket(snapshot: PremarketSnapshot, policy: PremarketPolicy) -> PremarketReport:
    """Evaluate all pre-session requirements; every absent/invalid mandatory item blocks the session."""
    if type(snapshot.session_date) is not date:
        raise PremarketError("session_date must be a date")
    if (not isinstance(snapshot.checked_at, datetime) or snapshot.checked_at.tzinfo is None
            or snapshot.checked_at.utcoffset() is None):
        raise PremarketError("checked_at must be timezone-aware")
    checked = snapshot.checked_at.astimezone(timezone.utc)
    checks: list[Check] = []

    def add(name: str, condition: bool, failure: str) -> None:
        checks.append(Check(name, bool(condition), "OK" if condition else failure))

    def present(value: str | None) -> bool:
        return isinstance(value, str) and bool(value.strip())

    add("session_id", present(snapshot.session_id), "session ID is missing")
    add("market_calendar", snapshot.calendar_is_session is True and present(snapshot.calendar_version)
        and present(snapshot.calendar_evidence_id), "market calendar/session evidence is missing or not tradable")
    add("data_entitlement", snapshot.data_entitlement_active is True and present(snapshot.data_source)
        and present(snapshot.entitlement_evidence_id), "active data entitlement/source evidence is missing")
    add("broker_read_only", snapshot.broker_read_only_session is True and present(snapshot.broker_session_id)
        and present(snapshot.broker_evidence_id), "verified read-only broker session evidence is missing")
    add("disk_capacity", type(snapshot.disk_free_bytes) is int
        and snapshot.disk_free_bytes >= policy.min_disk_free_bytes and present(snapshot.disk_probe_evidence_id),
        "disk space is below policy or disk probe evidence is missing")
    add("archive_writable", snapshot.archive_writable is True and present(snapshot.archive_probe_evidence_id),
        "archive write probe did not pass or evidence is missing")
    cost_dates_ok = (type(snapshot.cost_effective_from) is date
                     and snapshot.cost_effective_from <= snapshot.session_date
                     and (snapshot.cost_effective_until is None or type(snapshot.cost_effective_until) is date)
                     and (snapshot.cost_effective_until is None
                          or snapshot.session_date <= snapshot.cost_effective_until))
    add("cost_schedule", isinstance(snapshot.cost_schedule_sha256, str)
        and bool(_SHA256.fullmatch(snapshot.cost_schedule_sha256)) and cost_dates_ok
        and present(snapshot.cost_evidence_id), "effective cost schedule/hash evidence is missing or stale")
    add("strategy_version", present(snapshot.strategy_id) and present(snapshot.strategy_version)
        and isinstance(snapshot.strategy_spec_sha256, str)
        and bool(_SHA256.fullmatch(snapshot.strategy_spec_sha256)) and present(snapshot.strategy_evidence_id),
        "frozen strategy identity/version evidence is missing")
    add("risk_configuration", isinstance(snapshot.risk_config_sha256, str)
        and bool(_SHA256.fullmatch(snapshot.risk_config_sha256)) and snapshot.risk_approved is True
        and present(snapshot.risk_evidence_id), "approved risk configuration/evidence is missing")
    skew = snapshot.clock_skew_ms
    skew_ok = (type(skew) in (int, float) and math.isfinite(skew)
               and abs(skew) <= policy.max_clock_skew_ms and present(snapshot.clock_evidence_id))
    add("clocks", skew_ok, "clock skew exceeds policy or clock evidence is missing/invalid")

    canonical = {
        "session_id": snapshot.session_id,
        "session_date": snapshot.session_date.isoformat(),
        "checked_at_utc": checked.isoformat(),
        "policy_sha256": policy.policy_sha256,
        "snapshot": {
            key: value.isoformat() if isinstance(value, (datetime, date)) else value
            for key, value in asdict(snapshot).items()
        },
        "checks": [asdict(check) for check in checks],
    }
    # Datetime values were normalized above; invalid floats are represented textually so failures remain hashable.
    for key, value in canonical["snapshot"].items():
        if isinstance(value, float) and not math.isfinite(value):
            canonical["snapshot"][key] = repr(value)
    report_hash = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"),
                                           allow_nan=False).encode()).hexdigest()
    return PremarketReport(snapshot.session_id, snapshot.session_date, checked,
                           policy.policy_sha256, tuple(checks), report_hash)
