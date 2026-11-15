"""Durable immutable preregistration records for edge confirmation trials."""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

from .custody import Receipt
from .durable import DurableJournal


class EdgePreregistrationError(ValueError):
    """A frozen confirmation protocol is missing, malformed, or replaced."""


def _canonical(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise EdgePreregistrationError("preregistration must be canonical JSON") from exc


def _timestamp(value: str, label: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise EdgePreregistrationError(f"{label} must be timezone-aware ISO time") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise EdgePreregistrationError(f"{label} must be timezone-aware ISO time")
    return result.astimezone(timezone.utc)


def _receipt_payload(receipt: Receipt) -> dict:
    return {
        "provider_id": receipt.provider_id,
        "key_id": receipt.key_id,
        "digest_sha256": receipt.digest_sha256,
        "anchored_at_utc": receipt.anchored_at_utc.astimezone(timezone.utc).isoformat(),
        "signature_b64": receipt.signature_b64,
    }


class EdgePreregistrationRegistry:
    """Append-only freeze of protocol identity before the first outcome."""

    def __init__(self, path: Path) -> None:
        self.journal = DurableJournal(Path(path))
        self._records: dict[str, dict] = {}
        self._rebuild()

    @staticmethod
    def _identity(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise EdgePreregistrationError("trial_id is required")
        return value.strip()

    def _validate(self, record: dict) -> dict:
        trial_id = self._identity(record["trial_id"])
        protocol = record["protocol_sha256"]
        if not isinstance(protocol, str) or len(protocol) != 64 or any(
            c not in "0123456789abcdef" for c in protocol
        ):
            raise EdgePreregistrationError("protocol hash must be lowercase SHA-256")
        sessions = tuple(record["scheduled_sessions"])
        try:
            dates = tuple(date.fromisoformat(day).isoformat() for day in sessions)
        except (TypeError, ValueError) as exc:
            raise EdgePreregistrationError("schedule must contain ISO dates") from exc
        if not dates or dates != sessions or dates != tuple(sorted(set(dates))):
            raise EdgePreregistrationError("schedule must be nonempty, unique and ordered")
        registered = _timestamp(record["registered_at_utc"], "registered_at_utc")
        first = _timestamp(record["first_observation_at_utc"], "first_observation_at_utc")
        if registered >= first:
            raise EdgePreregistrationError("protocol must be registered before observations")
        receipt = record["registration_receipt"]
        if (not isinstance(receipt, dict) or receipt.get("digest_sha256") != protocol
                or not receipt.get("provider_id") or not receipt.get("key_id")
                or not receipt.get("signature_b64")):
            raise EdgePreregistrationError("registration receipt must attest the frozen protocol")
        anchored = _timestamp(receipt["anchored_at_utc"], "receipt anchor")
        if anchored >= first:
            raise EdgePreregistrationError("receipt must be anchored before first observation")
        payload = {
            "trial_id": trial_id, "protocol_sha256": protocol,
            "scheduled_sessions": list(dates),
            "registered_at_utc": registered.isoformat(),
            "first_observation_at_utc": first.isoformat(),
            "registration_receipt": receipt,
        }
        digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
        if record.get("evidence_sha256") != digest:
            raise EdgePreregistrationError("preregistration evidence digest mismatch")
        return {**payload, "evidence_sha256": digest}

    def _accept(self, record: dict) -> None:
        value = self._validate(record)
        trial_id = value["trial_id"]
        if trial_id in self._records and self._records[trial_id] != value:
            raise EdgePreregistrationError("trial preregistration is immutable")
        self._records[trial_id] = value

    def _rebuild(self) -> None:
        for row in self.journal.records():
            if row.get("t") != "EDGE_PREREGISTERED":
                raise EdgePreregistrationError("unknown edge preregistration event")
            try:
                self._accept(row["record"])
            except (KeyError, TypeError) as exc:
                raise EdgePreregistrationError("malformed edge preregistration event") from exc

    def register(self, *, trial_id: str, protocol_sha256: str,
                 scheduled_sessions: list[str], registered_at_utc: str,
                 first_observation_at_utc: str, registration_receipt: Receipt) -> dict:
        trial_id = self._identity(trial_id)
        if not isinstance(registration_receipt, Receipt):
            raise EdgePreregistrationError("independent registration receipt is required")
        record = {
            "trial_id": trial_id, "protocol_sha256": protocol_sha256,
            "scheduled_sessions": list(scheduled_sessions),
            "registered_at_utc": _timestamp(registered_at_utc, "registered_at_utc").isoformat(),
            "first_observation_at_utc": _timestamp(
                first_observation_at_utc, "first_observation_at_utc"
            ).isoformat(),
            "registration_receipt": _receipt_payload(registration_receipt),
        }
        record["evidence_sha256"] = hashlib.sha256(
            _canonical(record).encode("utf-8")
        ).hexdigest()
        prior = self._records.get(trial_id)
        if prior is not None and (
            prior["protocol_sha256"] != record["protocol_sha256"]
            or prior["scheduled_sessions"] != record["scheduled_sessions"]
            or prior["registered_at_utc"] != record["registered_at_utc"]
            or prior["first_observation_at_utc"] != record["first_observation_at_utc"]
            or prior["registration_receipt"] != record["registration_receipt"]
        ):
            raise EdgePreregistrationError("trial preregistration is immutable")
        value = self._validate(record)
        if prior is not None:
            if prior != value:
                raise EdgePreregistrationError("trial preregistration is immutable")
            return dict(prior)
        self.journal.append({"t": "EDGE_PREREGISTERED", "record": value})
        self._records[trial_id] = value
        return dict(value)

    def resolve(self, trial_id: str) -> dict:
        trial_id = self._identity(trial_id)
        try:
            return dict(self._validate(self._records[trial_id]))
        except KeyError as exc:
            raise EdgePreregistrationError("trial is not durably preregistered") from exc
