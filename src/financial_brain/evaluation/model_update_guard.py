"""Append-only model identity locks for prospective forward cohorts."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any
import re


class ModelUpdateGuardError(ValueError):
    """A model mutation conflicts with a frozen forward-cohort identity."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def model_identity_sha256(*, version: str, features: tuple[str, ...], weights: list[float], bias: float,
                          threshold: float | None, fitted_on: tuple[str, str],
                          verified_on: tuple[str, str]) -> str:
    values = [*weights, bias, *( [] if threshold is None else [threshold] )]
    if any(not math.isfinite(value) for value in values):
        raise ModelUpdateGuardError("model weights and threshold must be finite")
    body = {"version": version, "features": list(features), "weights": list(weights), "bias": bias,
            "threshold": threshold, "fitted_on": list(fitted_on), "verified_on": list(verified_on)}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode("utf-8")).hexdigest()


def ensure_schema(con) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS model_cohort_update_journal(
                    sequence BIGINT PRIMARY KEY, record_json VARCHAR NOT NULL, record_sha256 VARCHAR NOT NULL)""")


def _records(con) -> list[dict[str, Any]]:
    ensure_schema(con)
    rows = con.execute("SELECT sequence, record_json, record_sha256 FROM model_cohort_update_journal ORDER BY sequence").fetchall()
    previous = "0" * 64
    out: list[dict[str, Any]] = []
    for expected_sequence, raw, claimed in rows:
        record = json.loads(raw)
        digest = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":"),
                                           allow_nan=False).encode("utf-8")).hexdigest()
        if (expected_sequence != len(out) + 1 or record.get("sequence") != expected_sequence
                or record.get("previous_record_sha256") != previous or digest != claimed):
            raise ModelUpdateGuardError("model-cohort update journal hash chain is invalid")
        previous = claimed
        out.append({**record, "record_sha256": claimed})
    return out


def _active(con) -> tuple[dict[str, dict[str, Any]], set[str]]:
    active: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for record in _records(con):
        cohort_id, kind = record["cohort_id"], record["event"]
        if kind == "COHORT_LOCKED":
            if cohort_id in seen:
                raise ModelUpdateGuardError("cohort identity was locked more than once")
            seen.add(cohort_id)
            active[cohort_id] = record
        elif kind == "COHORT_DRIFT_ALARM":
            if cohort_id not in active or record["model_sha256"] != active[cohort_id]["model_sha256"]:
                raise ModelUpdateGuardError("drift alarm does not match an active cohort model")
        elif kind == "COHORT_CLOSED":
            if cohort_id not in active or record["model_sha256"] != active[cohort_id]["model_sha256"]:
                raise ModelUpdateGuardError("cohort close does not match its frozen model")
            del active[cohort_id]
        else:
            raise ModelUpdateGuardError("unknown model-cohort journal event")
    return active, seen


def _aware(value: datetime, label: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ModelUpdateGuardError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _append(con, *, event: str, cohort_id: str, model_sha256: str, at_utc: datetime,
            extra: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(cohort_id, str) or not cohort_id.strip():
        raise ModelUpdateGuardError("cohort_id is required")
    if not isinstance(model_sha256, str) or not _SHA256.fullmatch(model_sha256):
        raise ModelUpdateGuardError("model_sha256 must be a lowercase SHA-256")
    records = _records(con)
    previous = records[-1]["record_sha256"] if records else "0" * 64
    body = {"sequence": len(records) + 1, "event": event, "cohort_id": cohort_id,
            "model_sha256": model_sha256, "at_utc": _aware(at_utc, "at_utc"),
            "previous_record_sha256": previous, **extra}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                        allow_nan=False).encode("utf-8")).hexdigest()
    con.execute("INSERT INTO model_cohort_update_journal VALUES (?, ?, ?)",
                [body["sequence"], json.dumps(body, sort_keys=True, separators=(",", ":")), digest])
    return {**body, "record_sha256": digest}


def freeze_model_for_cohort(con, *, cohort_id: str, model_sha256: str,
                            protocol_sha256: str, locked_at: datetime) -> dict[str, Any]:
    """Freeze one exact model identity before the scheduled forward cohort starts."""
    if not isinstance(protocol_sha256, str) or not _SHA256.fullmatch(protocol_sha256):
        raise ModelUpdateGuardError("protocol_sha256 must be a lowercase SHA-256")
    active, seen = _active(con)
    if cohort_id in seen:
        raise ModelUpdateGuardError("cohort identity cannot be reused")
    return _append(con, event="COHORT_LOCKED", cohort_id=cohort_id,
                   model_sha256=model_sha256, at_utc=locked_at,
                   extra={"protocol_sha256": protocol_sha256})


def record_drift_alarm(con, *, cohort_id: str, model_sha256: str,
                       drift_metric: float, alarm_id: str, at_utc: datetime) -> dict[str, Any]:
    """Record drift against the frozen model; the alarm never changes or unlocks it."""
    active, _ = _active(con)
    if cohort_id not in active or active[cohort_id]["model_sha256"] != model_sha256:
        raise ModelUpdateGuardError("drift alarm must reference the active frozen model")
    if not alarm_id.strip() or not math.isfinite(drift_metric):
        raise ModelUpdateGuardError("drift alarm needs a finite metric and stable alarm ID")
    return _append(con, event="COHORT_DRIFT_ALARM", cohort_id=cohort_id,
                   model_sha256=model_sha256, at_utc=at_utc,
                   extra={"drift_metric": drift_metric, "alarm_id": alarm_id})


def close_cohort(con, *, cohort_id: str, model_sha256: str,
                 closure_evidence_sha256: str, closed_at: datetime) -> dict[str, Any]:
    active, _ = _active(con)
    if cohort_id not in active or active[cohort_id]["model_sha256"] != model_sha256:
        raise ModelUpdateGuardError("cohort close must match its active frozen model")
    if not isinstance(closure_evidence_sha256, str) or not _SHA256.fullmatch(closure_evidence_sha256):
        raise ModelUpdateGuardError("closure evidence digest is required")
    return _append(con, event="COHORT_CLOSED", cohort_id=cohort_id,
                   model_sha256=model_sha256, at_utc=closed_at,
                   extra={"closure_evidence_sha256": closure_evidence_sha256})


def assert_model_update_allowed(con, *, proposed_model_sha256: str) -> bool:
    """Return true if saving is allowed; same-identity writes during a lock become no-ops."""
    if not isinstance(proposed_model_sha256, str) or not _SHA256.fullmatch(proposed_model_sha256):
        raise ModelUpdateGuardError("proposed model identity must be a lowercase SHA-256")
    active, _ = _active(con)
    conflicts = [cohort for cohort, lock in active.items() if lock["model_sha256"] != proposed_model_sha256]
    if conflicts:
        raise ModelUpdateGuardError(f"model update blocked by locked forward cohort {sorted(conflicts)[0]}")
    return not active


def active_cohorts(con) -> dict[str, str]:
    active, _ = _active(con)
    return {cohort: record["model_sha256"] for cohort, record in active.items()}
