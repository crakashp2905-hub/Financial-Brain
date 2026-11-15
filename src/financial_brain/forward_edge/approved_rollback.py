"""Durable, approval-scoped rollback transitions for paper strategy families.

This journal records approvals and alarms as immutable, hash-chained events. It only
supports paper-book policy; it cannot submit broker orders or authorize live capital.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .approvals import Approval, ApprovalError, validate_approval

_SHA = re.compile(r"^[0-9a-f]{64}$")


class RollbackError(ValueError):
    """A rollback or its evidence is invalid or lacks authorization."""


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                  allow_nan=False).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> None:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise RollbackError(f"{label} must be a lowercase SHA-256")


def _money(value: Decimal | str | int) -> Decimal:
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise RollbackError("capital must be a finite non-negative decimal") from exc
    if not result.is_finite() or result < 0:
        raise RollbackError("capital must be a finite non-negative decimal")
    return result


def version_approval_scope_sha256(*, family_id: str, version_id: str,
                                  artifact_sha256: str, capital_inr: Decimal | str | int) -> str:
    _sha(artifact_sha256, "artifact_sha256")
    body = {"action": "APPROVE_VERSION", "family_id": family_id,
            "version_id": version_id, "artifact_sha256": artifact_sha256,
            "capital_inr": format(_money(capital_inr).normalize(), "f")}
    return _digest(body)


def rollback_scope_sha256(*, family_id: str, from_version: str, to_version: str,
                          current_capital_inr: Decimal | str | int,
                          requested_capital_inr: Decimal | str | int,
                          alarm_evidence_sha256: tuple[str, ...]) -> str:
    if not family_id.strip() or not from_version.strip() or not to_version.strip():
        raise RollbackError("family and version identities are required")
    for digest in alarm_evidence_sha256:
        _sha(digest, "alarm evidence digest")
    body = {"action": "ROLLBACK", "family_id": family_id,
            "from_version": from_version, "to_version": to_version,
            "current_capital_inr": format(_money(current_capital_inr).normalize(), "f"),
            "requested_capital_inr": format(_money(requested_capital_inr).normalize(), "f"),
            "alarm_evidence_sha256": sorted(alarm_evidence_sha256)}
    return _digest(body)


def rollback_capital_scope_sha256(*, rollback_scope: str,
                                  current_capital_inr: Decimal | str | int,
                                  requested_capital_inr: Decimal | str | int) -> str:
    _sha(rollback_scope, "rollback_scope")
    return _digest({"action": "ROLLBACK_CAPITAL", "rollback_scope_sha256": rollback_scope,
                    "current_capital_inr": format(_money(current_capital_inr).normalize(), "f"),
                    "requested_capital_inr": format(_money(requested_capital_inr).normalize(), "f")})


def _ensure(con) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS approved_rollback_journal(
        sequence BIGINT PRIMARY KEY, record_json VARCHAR NOT NULL,
        record_sha256 VARCHAR NOT NULL)""")


def _records(con) -> list[dict[str, Any]]:
    _ensure(con)
    rows = con.execute("SELECT sequence, record_json, record_sha256 FROM approved_rollback_journal ORDER BY sequence").fetchall()
    previous = "0" * 64
    output: list[dict[str, Any]] = []
    for sequence, raw, claimed in rows:
        record = json.loads(raw)
        if (sequence != len(output) + 1 or record.get("sequence") != sequence
                or record.get("previous_record_sha256") != previous
                or _digest(record) != claimed):
            raise RollbackError("rollback journal hash chain is invalid")
        previous = claimed
        output.append({**record, "record_sha256": claimed})
    return output


def _state(con, family_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    versions: list[dict[str, Any]] = []
    alarms: list[dict[str, Any]] = []
    for event in _records(con):
        if event["family_id"] != family_id:
            continue
        if event["event"] == "VERSION_APPROVED":
            if any(row["version_id"] == event["version_id"] for row in versions):
                raise RollbackError("approved version identity is duplicated")
            versions.append(event)
            alarms.clear()
        elif event["event"] == "ALARM":
            if not versions or event["version_id"] != versions[-1]["version_id"]:
                raise RollbackError("alarm must reference the active approved version")
            alarms.append(event)
        elif event["event"] == "ROLLBACK":
            if len(versions) < 2 or event["from_version"] != versions[-1]["version_id"]:
                raise RollbackError("rollback source is not the active approved version")
            if event["to_version"] != versions[-2]["version_id"]:
                raise RollbackError("rollback target is not the preceding approved version")
            if not alarms or event["alarm_evidence_sha256"] != sorted(a["evidence_sha256"] for a in alarms):
                raise RollbackError("rollback does not cover the active version's alarm evidence")
            versions.pop()
            versions[-1] = {**versions[-1], "capital_inr": event["requested_capital_inr"]}
            alarms.clear()
        else:
            raise RollbackError("unknown rollback journal event")
    return versions, alarms


def _append(con, record: dict[str, Any]) -> dict[str, Any]:
    records = _records(con)
    body = {"sequence": len(records) + 1,
            "previous_record_sha256": records[-1]["record_sha256"] if records else "0" * 64,
            **record}
    digest = _digest(body)
    con.execute("INSERT INTO approved_rollback_journal VALUES (?, ?, ?)",
                [body["sequence"], json.dumps(body, sort_keys=True, separators=(",", ":")), digest])
    return {**body, "record_sha256": digest}


def approve_version(con, *, family_id: str, version_id: str, artifact_sha256: str,
                    capital_inr: Decimal | str | int, approval: Approval,
                    capital_approval: Approval | None = None,
                    now: datetime) -> dict[str, Any]:
    """Record an approved paper version; changes in capital also require CAPITAL approval."""
    if not family_id.strip() or not version_id.strip():
        raise RollbackError("family_id and version_id are required")
    _sha(artifact_sha256, "artifact_sha256")
    capital = _money(capital_inr)
    versions, _ = _state(con, family_id)
    ever_approved = [row for row in _records(con)
                     if row.get("family_id") == family_id and row.get("event") == "VERSION_APPROVED"]
    if any(row["version_id"] == version_id for row in ever_approved):
        raise RollbackError("version ID has already been approved")
    scope = version_approval_scope_sha256(family_id=family_id, version_id=version_id,
                                          artifact_sha256=artifact_sha256, capital_inr=capital)
    try:
        validate_approval(approval, kind="STRATEGY_ENROLLMENT", subject=family_id,
                          scope_sha256=scope, now=now)
        if versions and capital != Decimal(versions[-1]["capital_inr"]):
            if capital_approval is None:
                raise RollbackError("capital change requires separate CAPITAL approval")
            validate_approval(capital_approval, kind="CAPITAL", subject=family_id,
                              scope_sha256=scope, now=now)
    except ApprovalError as exc:
        raise RollbackError(f"version approval rejected: {exc}") from exc
    if now.tzinfo is None or now.utcoffset() is None:
        raise RollbackError("now must be timezone-aware")
    return _append(con, {"event": "VERSION_APPROVED", "family_id": family_id,
                         "version_id": version_id, "artifact_sha256": artifact_sha256,
                         "capital_inr": format(capital, "f"), "scope_sha256": scope,
                         "approval_actor_id": approval.actor_id,
                         "approved_at_utc": now.isoformat()})


def record_alarm(con, *, family_id: str, version_id: str, alarm_kind: str,
                 evidence_sha256: str, at_utc) -> dict[str, Any]:
    """Record forecast-drift or harm evidence for the currently approved version."""
    if alarm_kind not in {"FORECAST_DRIFT", "HARM"}:
        raise RollbackError("alarm_kind must be FORECAST_DRIFT or HARM")
    _sha(evidence_sha256, "evidence_sha256")
    if at_utc.tzinfo is None or at_utc.utcoffset() is None:
        raise RollbackError("alarm timestamp must be timezone-aware")
    versions, _ = _state(con, family_id)
    if not versions or versions[-1]["version_id"] != version_id:
        raise RollbackError("alarm must target the active approved version")
    return _append(con, {"event": "ALARM", "family_id": family_id,
                         "version_id": version_id, "alarm_kind": alarm_kind,
                         "evidence_sha256": evidence_sha256,
                         "at_utc": at_utc.isoformat()})


def rollback_to_previous_approved(con, *, family_id: str, requested_capital_inr: Decimal | str | int,
                                  approval: Approval, capital_approval: Approval | None,
                                  now: datetime) -> dict[str, Any]:
    """Roll back exactly one approved version after an alarm, with separately scoped capital consent."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise RollbackError("now must be timezone-aware")
    versions, alarms = _state(con, family_id)
    if len(versions) < 2:
        raise RollbackError("no preceding approved version is available")
    if not alarms:
        raise RollbackError("rollback requires forecast-drift or harm alarm evidence")
    current, target = versions[-1], versions[-2]
    current_capital = Decimal(current["capital_inr"])
    requested_capital = _money(requested_capital_inr)
    alarm_digests = tuple(sorted(a["evidence_sha256"] for a in alarms))
    scope = rollback_scope_sha256(
        family_id=family_id, from_version=current["version_id"], to_version=target["version_id"],
        current_capital_inr=current_capital, requested_capital_inr=requested_capital,
        alarm_evidence_sha256=alarm_digests)
    try:
        validate_approval(approval, kind="ROLLBACK", subject=family_id,
                          scope_sha256=scope, now=now)
        if requested_capital != current_capital:
            if capital_approval is None:
                raise RollbackError("capital change requires separate CAPITAL approval")
            capital_scope = rollback_capital_scope_sha256(
                rollback_scope=scope, current_capital_inr=current_capital,
                requested_capital_inr=requested_capital)
            validate_approval(capital_approval, kind="CAPITAL", subject=family_id,
                              scope_sha256=capital_scope, now=now)
    except ApprovalError as exc:
        raise RollbackError(f"rollback approval rejected: {exc}") from exc
    return _append(con, {"event": "ROLLBACK", "family_id": family_id,
                         "from_version": current["version_id"], "to_version": target["version_id"],
                         "current_capital_inr": format(current_capital, "f"),
                         "requested_capital_inr": format(requested_capital, "f"),
                         "alarm_evidence_sha256": list(alarm_digests), "scope_sha256": scope,
                         "approval_actor_id": approval.actor_id, "approved_at_utc": now.isoformat()})


def active_version(con, *, family_id: str) -> dict[str, Any] | None:
    versions, _ = _state(con, family_id)
    if not versions:
        return None
    active = versions[-1]
    return {"version_id": active["version_id"], "artifact_sha256": active["artifact_sha256"],
            "capital_inr": active["capital_inr"]}
