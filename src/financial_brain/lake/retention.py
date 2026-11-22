"""Reviewed, scoped deletion of raw-lake payloads with a durable provenance tombstone.

Raw payloads are write-once by default. A retention directive is an explicit exception:
it may remove one named source/dataset payload after its retention deadline, while the
original sidecar and a tombstone preserve the deleted object's identity and checksum.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .store import LakeObject


class RetentionError(ValueError):
    """Retention directive or raw-lake object does not satisfy deletion controls."""


class RedactedPayloadError(RetentionError):
    """The object payload was deleted under a reviewed retention directive."""


@dataclass(frozen=True)
class RetentionDirective:
    directive_id: str
    source: str
    dataset: str
    delete_after: datetime
    authority_ref: str
    reviewed_by: str
    reason: str

    def __post_init__(self) -> None:
        for name in ("directive_id", "source", "dataset", "authority_ref", "reviewed_by", "reason"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise RetentionError(f"{name} is required")
        if self.delete_after.tzinfo is None or self.delete_after.utcoffset() is None:
            raise RetentionError("delete_after must be timezone-aware")
        object.__setattr__(self, "delete_after", self.delete_after.astimezone(timezone.utc))


@dataclass(frozen=True)
class RedactionReceipt:
    schema: str
    object_key: str
    source: str
    dataset: str
    business_date: str
    filename: str
    url: str
    retrieved_at: str
    original_sha256: str
    original_size_bytes: int
    content_type: str
    http_status: int
    directive_id: str
    authority_ref: str
    reviewed_by: str
    reason: str
    delete_after: str
    redacted_at: str
    receipt_sha256: str = ""

    def signed_payload(self) -> dict[str, object]:
        value = asdict(self)
        value.pop("receipt_sha256")
        return value


def redact_payload(root: Path, object_key: str, directive: RetentionDirective, *,
                   now: datetime) -> RedactionReceipt:
    """Delete one payload after its directive deadline and preserve a hash-stamped tombstone.

    Replaying the same directive is idempotent. The function never recursively deletes and
    refuses objects outside the lake root, wrong-scope directives, unreviewed directives,
    early deletion, and payloads that do not match their immutable metadata hash.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise RetentionError("now must be timezone-aware")
    now_utc = now.astimezone(timezone.utc)
    if now_utc < directive.delete_after:
        raise RetentionError("retention deadline has not elapsed")
    if not directive.reviewed_by.strip() or not directive.authority_ref.strip():
        raise RetentionError("deletion requires a reviewer and authority reference")

    key = PurePosixPath(object_key)
    if key.is_absolute() or not key.parts or any(part in {"", ".", ".."} for part in key.parts):
        raise RetentionError("object key must be a safe relative lake key")
    base = Path(root).resolve()
    payload_path = (base / Path(*key.parts)).resolve()
    if not payload_path.is_relative_to(base):
        raise RetentionError("object key escapes the lake root")
    meta_path = Path(str(payload_path) + ".meta.json")
    receipt_path = Path(str(payload_path) + ".redacted.json")
    if not meta_path.is_file():
        raise RetentionError("object metadata is missing; refusing uncontrolled deletion")

    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    obj = LakeObject(**metadata)
    if obj.key != key.as_posix():
        raise RetentionError("object key does not match immutable metadata")
    if (obj.source, obj.dataset) != (directive.source, directive.dataset):
        raise RetentionError("retention directive scope does not match the object")

    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt_obj = RedactionReceipt(**receipt)
        digest = hashlib.sha256(json.dumps(
            receipt_obj.signed_payload(), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False).encode("utf-8")).hexdigest()
        if digest != receipt_obj.receipt_sha256:
            raise RetentionError("redaction receipt checksum mismatch")
        if (receipt_obj.directive_id != directive.directive_id
                or (receipt_obj.source, receipt_obj.dataset) != (directive.source, directive.dataset)
                or receipt_obj.authority_ref != directive.authority_ref
                or receipt_obj.reviewed_by != directive.reviewed_by
                or receipt_obj.reason != directive.reason
                or receipt_obj.delete_after != directive.delete_after.isoformat()):
            raise RetentionError("object already has a different redaction directive")
        if payload_path.exists():
            replay_payload = payload_path.read_bytes()
            if (hashlib.sha256(replay_payload).hexdigest() != receipt_obj.original_sha256
                    or len(replay_payload) != receipt_obj.original_size_bytes):
                raise RetentionError("payload after redaction does not match tombstone; refusing deletion")
            payload_path.unlink()
        return receipt_obj

    if not payload_path.is_file():
        raise RetentionError("payload is absent without a matching redaction receipt")
    payload = payload_path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != obj.sha256 or len(payload) != obj.size_bytes:
        raise RetentionError("payload does not match immutable metadata; refusing deletion")

    receipt = RedactionReceipt(
        schema="fb.raw-lake-redaction.v1", object_key=obj.key, source=obj.source, dataset=obj.dataset,
        business_date=obj.business_date, filename=obj.filename, url=obj.url, retrieved_at=obj.retrieved_at,
        original_sha256=obj.sha256, original_size_bytes=obj.size_bytes, content_type=obj.content_type,
        http_status=obj.http_status, directive_id=directive.directive_id,
        authority_ref=directive.authority_ref, reviewed_by=directive.reviewed_by, reason=directive.reason,
        delete_after=directive.delete_after.isoformat(), redacted_at=now_utc.isoformat(),
    )
    receipt = RedactionReceipt(**{**asdict(receipt), "receipt_sha256": hashlib.sha256(
        json.dumps(receipt.signed_payload(), sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False).encode("utf-8")).hexdigest()})

    # Write the tombstone first. If the process stops before unlink, retry with the same
    # directive completes deletion; readers can be taught to fail closed on this marker.
    temporary = receipt_path.with_name(receipt_path.name + ".tmp")
    temporary.write_text(json.dumps(asdict(receipt), sort_keys=True, indent=2), encoding="utf-8")
    temporary.replace(receipt_path)
    payload_path.unlink()
    return receipt


__all__ = ["RedactedPayloadError", "RedactionReceipt", "RetentionDirective", "RetentionError", "redact_payload"]
