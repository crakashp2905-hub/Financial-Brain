"""Immutable first-loss evidence capsules for postmortem capture.

This local package is tamper-evident, not independently timestamped or externally
attested. Callers must capture the source bytes before attempting repair.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Mapping

REQUIRED_EVIDENCE = frozenset({"quote", "signal", "risk", "execution", "ledger"})
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class IncidentEvidenceError(ValueError):
    """Invalid or conflicting incident evidence package."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_new(path: Path, data: bytes) -> None:
    """Durably create a file without replacing any existing evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        with temp.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise IncidentEvidenceError(f"immutable evidence conflict at {path.name}") from None
        except PermissionError:
            # Some Windows filesystems/sandbox mounts disallow hard links. Fall back
            # to exclusive creation, which still refuses to replace existing evidence.
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                if path.read_bytes() != data:
                    raise IncidentEvidenceError(
                        f"immutable evidence conflict at {path.name}") from None
            else:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
        temp.unlink(missing_ok=True)
        if os.name == "posix":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temp.unlink(missing_ok=True)


def capture_first_loss(
    root: Path,
    *,
    incident_id: str,
    occurred_at: str,
    evidence: Mapping[str, bytes],
) -> dict[str, object]:
    """Persist raw evidence bytes and a hash manifest for one incident exactly once.

    ``evidence`` must contain the five canonical evidence categories. Blob storage is
    content-addressed; an existing incident ID may only be replayed with identical
    content. This function intentionally does not perform repairs or claim an external
    time attestation.
    """
    if not _ID.fullmatch(incident_id):
        raise IncidentEvidenceError("incident_id must be a safe, non-empty identifier")
    if not occurred_at.strip():
        raise IncidentEvidenceError("incident occurrence time is required")
    if set(evidence) != REQUIRED_EVIDENCE:
        missing = sorted(REQUIRED_EVIDENCE - set(evidence))
        extra = sorted(set(evidence) - REQUIRED_EVIDENCE)
        raise IncidentEvidenceError(f"evidence categories mismatch; missing={missing}, extra={extra}")
    if any(not isinstance(value, bytes) or not value for value in evidence.values()):
        raise IncidentEvidenceError("every raw evidence artifact must be non-empty bytes")

    root = Path(root)
    blob_dir = root / "blobs"
    records: dict[str, dict[str, str | int]] = {}
    for category in sorted(REQUIRED_EVIDENCE):
        payload = evidence[category]
        digest = _sha256(payload)
        # Keep paths short enough for Windows worktrees with long sandbox temp roots;
        # a prefix collision is detected by _write_new and fails closed.
        blob_name = f"{digest[:24]}.bin"
        _write_new(blob_dir / blob_name, payload)
        records[category] = {"sha256": digest, "size_bytes": len(payload),
                             "blob": f"blobs/{blob_name}"}
    body = {"schema": "fb.first_loss_incident_evidence.v1", "incident_id": incident_id,
            "occurred_at": occurred_at, "claim_scope": "LOCAL_TAMPER_EVIDENT_ONLY",
            "evidence": records}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    manifest = {**body, "manifest_sha256": _sha256(canonical)}
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    manifest_path = root / "incidents" / f"{incident_id}.json"
    _write_new(manifest_path, manifest_bytes)
    return manifest


def verify_first_loss(root: Path, incident_id: str) -> bool:
    """Verify a capsule's manifest digest, required categories and every raw blob."""
    if not _ID.fullmatch(incident_id):
        raise IncidentEvidenceError("incident_id must be a safe, non-empty identifier")
    try:
        manifest = json.loads((Path(root) / "incidents" / f"{incident_id}.json").read_text("utf-8"))
        digest = manifest.pop("manifest_sha256")
        body_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                                allow_nan=False).encode()
        if (_sha256(body_bytes) != digest or manifest["incident_id"] != incident_id
                or set(manifest["evidence"]) != REQUIRED_EVIDENCE):
            return False
        for item in manifest["evidence"].values():
            blob = (Path(root) / item["blob"]).read_bytes()
            if len(blob) != item["size_bytes"] or _sha256(blob) != item["sha256"]:
                return False
        return True
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
