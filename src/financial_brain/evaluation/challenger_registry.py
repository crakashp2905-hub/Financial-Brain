"""Append-only, content-addressed model challenger registry with gated promotion."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from financial_brain.forward_edge.approvals import Approval, ApprovalError, validate_approval


class ChallengerRegistryError(ValueError):
    """Invalid registry record, chain, signature, or promotion evidence."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ZERO_HASH = "0" * 64
_SCHEMA = "fb.challenger-registry.v1"
_PROMOTION_REQUIREMENTS = (
    "status", "preauthorized", "prospective_only", "verified_sessions", "independent_receipt_coverage",
    "net_alpha_lcb_bps", "matched_control_lcb_bps", "cost_stress_passed",
    "multiplicity_adjusted", "candidate_count", "evidence_sha256",
)


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _aware(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ChallengerRegistryError("registry timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _hash(value: str, name: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ChallengerRegistryError(f"{name} must be a lowercase SHA-256")


def _signature(body_sha256: str, *, signing_key: bytes | None, key_id: str | None) -> dict[str, str] | None:
    if signing_key is None:
        if key_id is not None:
            raise ChallengerRegistryError("key_id requires a signing key")
        return None
    if not isinstance(signing_key, bytes) or len(signing_key) < 32 or not key_id:
        raise ChallengerRegistryError("signing requires a key of at least 32 bytes and a key_id")
    signature = hmac.new(signing_key, body_sha256.encode("ascii"), hashlib.sha256).hexdigest()
    return {"algorithm": "HMAC-SHA256", "key_id": key_id, "value": signature}


def verify_registry(path: Path, *, verification_keys: dict[str, bytes] | None = None) -> dict[str, Any]:
    """Verify record hashes, append order, and any supplied HMAC signatures."""
    previous = _ZERO_HASH
    records: list[dict[str, Any]] = []
    seen_models: set[str] = set()
    keys = verification_keys or {}
    if not path.exists():
        return {"valid": True, "records": records, "head_sha256": previous, "signature_verified": True}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ChallengerRegistryError("registry cannot be read") from exc
    for line_number, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
            signature = record.pop("signature", None)
            claimed_hash = record.pop("record_sha256")
            if record.get("previous_record_sha256") != previous or _digest(record) != claimed_hash:
                raise ChallengerRegistryError(f"registry chain/hash mismatch at line {line_number}")
            if record.get("schema_version") != _SCHEMA:
                raise ChallengerRegistryError(f"unsupported registry schema at line {line_number}")
            if signature is not None:
                key = keys.get(signature.get("key_id"))
                if key is None or signature.get("algorithm") != "HMAC-SHA256":
                    raise ChallengerRegistryError(f"signature key unavailable at line {line_number}")
                actual = hmac.new(key, claimed_hash.encode("ascii"), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(actual, signature.get("value", "")):
                    raise ChallengerRegistryError(f"signature mismatch at line {line_number}")
            if record.get("event") == "CHALLENGER_REGISTERED":
                model_id = record.get("model_id")
                if not isinstance(model_id, str) or model_id in seen_models:
                    raise ChallengerRegistryError(f"duplicate or invalid model identity at line {line_number}")
                seen_models.add(model_id)
            records.append({**record, "record_sha256": claimed_hash, "signature": signature})
            previous = claimed_hash
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
            raise ChallengerRegistryError(f"malformed registry record at line {line_number}") from exc
    return {"valid": True, "records": records, "head_sha256": previous,
            "signature_verified": all(item["signature"] is not None for item in records)}


def _append(path: Path, body: dict[str, Any], *, signing_key: bytes | None, key_id: str | None) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    chain = verify_registry(path, verification_keys={key_id: signing_key} if key_id and signing_key else {})
    body = {**body, "previous_record_sha256": chain["head_sha256"]}
    record_hash = _digest(body)
    record = {**body, "record_sha256": record_hash,
              "signature": _signature(record_hash, signing_key=signing_key, key_id=key_id)}
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    try:
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
            stream.flush()
    except OSError as exc:
        raise ChallengerRegistryError("registry record could not be appended") from exc
    return record


def register_challenger(path: Path, *, model_id: str, model_sha256: str,
                        training_manifest_sha256: str, code_sha256: str, created_at: datetime,
                        signing_key: bytes | None = None, key_id: str | None = None) -> dict[str, Any]:
    """Register a trained artifact as challenger-only; training cannot set promotion status."""
    for value, name in ((model_id, "model_id"), (model_sha256, "model_sha256"),
                        (training_manifest_sha256, "training_manifest_sha256"), (code_sha256, "code_sha256")):
        _hash(value, name)
    chain = verify_registry(path, verification_keys={key_id: signing_key} if key_id and signing_key else {})
    if any(item.get("model_id") == model_id for item in chain["records"]):
        raise ChallengerRegistryError("model identity is already registered")
    return _append(path, {
        "schema_version": _SCHEMA, "event": "CHALLENGER_REGISTERED", "model_id": model_id,
        "model_sha256": model_sha256, "training_manifest_sha256": training_manifest_sha256,
        "code_sha256": code_sha256, "created_at_utc": _aware(created_at),
        "status": "CHALLENGER_ONLY", "auto_promotion_allowed": False,
    }, signing_key=signing_key, key_id=key_id)


def promotion_scope_sha256(*, model_id: str, evidence_sha256: str) -> str:
    _hash(model_id, "model_id")
    _hash(evidence_sha256, "evidence_sha256")
    return _digest({"action": "PROMOTE_CHALLENGER", "model_id": model_id,
                    "evidence_sha256": evidence_sha256})


def promote_challenger(path: Path, *, model_id: str, evidence: dict[str, Any],
                       approval: Approval, now: datetime, signing_key: bytes, key_id: str) -> dict[str, Any]:
    """Append promotion only with preauthorized prospective proof and a scoped human approval."""
    _aware(now)
    _hash(model_id, "model_id")
    missing = [field for field in _PROMOTION_REQUIREMENTS if field not in evidence]
    if missing:
        raise ChallengerRegistryError(f"promotion evidence missing {sorted(missing)}")
    _hash(evidence["evidence_sha256"], "evidence_sha256")
    if (evidence["status"] != "VERIFIED" or evidence["preauthorized"] is not True
            or evidence["prospective_only"] is not True
            or evidence["independent_receipt_coverage"] is not True
            or evidence["cost_stress_passed"] is not True
            or evidence["multiplicity_adjusted"] is not True
            or type(evidence["verified_sessions"]) is not int or evidence["verified_sessions"] < 60
            or type(evidence["candidate_count"]) is not int or evidence["candidate_count"] < 1
            or float(evidence["net_alpha_lcb_bps"]) <= 0
            or float(evidence["matched_control_lcb_bps"]) <= 0):
        raise ChallengerRegistryError("promotion requires authorized, prospective, verified, robust evidence")
    chain = verify_registry(path, verification_keys={key_id: signing_key})
    candidate = next((item for item in chain["records"] if item.get("model_id") == model_id
                      and item.get("event") == "CHALLENGER_REGISTERED"), None)
    if candidate is None:
        raise ChallengerRegistryError("model is not registered as a challenger")
    if any(item.get("model_id") == model_id and item.get("event") == "CHALLENGER_PROMOTED"
           for item in chain["records"]):
        raise ChallengerRegistryError("challenger is already promoted")
    scope = promotion_scope_sha256(model_id=model_id, evidence_sha256=evidence["evidence_sha256"])
    try:
        validate_approval(approval, kind="STRATEGY_ENROLLMENT", subject=model_id,
                          scope_sha256=scope, now=now)
    except ApprovalError as exc:
        raise ChallengerRegistryError(f"promotion requires a scoped human approval: {exc}") from exc
    return _append(path, {
        "schema_version": _SCHEMA, "event": "CHALLENGER_PROMOTED", "model_id": model_id,
        "candidate_record_sha256": candidate["record_sha256"],
        "evidence_sha256": evidence["evidence_sha256"],
        "verified_sessions": evidence["verified_sessions"], "candidate_count": evidence["candidate_count"],
        "approval_actor_id": approval.actor_id, "approval_scope_sha256": scope,
        "created_at_utc": _aware(now), "status": "PROMOTED",
    }, signing_key=signing_key, key_id=key_id)
