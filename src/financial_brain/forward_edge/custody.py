"""Independent decision custody (FE-0047, FE-0048, FE-0050, FE-0052, FE-0053).

Statuses: ``VERIFIED`` (a pinned trust root signed a receipt over the exact manifest digest, at or after the decision
and before the actionable entry time), ``LOCAL_ONLY`` (no external receipt - the honest fallback, never eligible for
a formal claim), ``UNVERIFIED`` (a receipt exists but cannot be verified against a pinned trust root, including any
self-signed or local signature), ``LATE`` (the receipt arrived at or after the earliest actionable entry) and
``CONFLICTED`` (digest, clock or ordering inconsistent: wrong digest, receipt before decision, or decision at or
after entry).

Only ``VERIFIED`` is eligible for a formal forward claim. A local signer may exist for development, but its receipts
classify as UNVERIFIED because the trust root is never the local key.
"""
from __future__ import annotations

import base64
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol

_SHA = re.compile(r"^[0-9a-f]{64}$")


class CustodyStatus(str, Enum):
    VERIFIED = "VERIFIED"
    LOCAL_ONLY = "LOCAL_ONLY"
    UNVERIFIED = "UNVERIFIED"
    LATE = "LATE"
    CONFLICTED = "CONFLICTED"


class ProviderNotConfigured(RuntimeError):
    """No independent timestamp provider is configured. Callers fall back to LOCAL_ONLY; no receipt is faked."""


@dataclass(frozen=True)
class Receipt:
    provider_id: str
    key_id: str
    digest_sha256: str
    anchored_at_utc: datetime
    signature_b64: str


@dataclass(frozen=True)
class TrustRoot:
    provider_id: str
    key_id: str
    public_key_b64: str  # raw 32-byte Ed25519 public key, pinned in configuration


class TimestampProvider(Protocol):
    provider_id: str

    def anchor(self, digest_sha256: str) -> Receipt:
        """Return a receipt or raise. Offline code never synthesizes a receipt for an independent provider."""


class UnconfiguredProvider:
    """The default. Refuses every request, so no independent receipt can exist without a configured provider."""

    provider_id = "UNCONFIGURED"

    def anchor(self, digest_sha256: str) -> Receipt:
        raise ProviderNotConfigured("INDEPENDENT_TIMESTAMP_RECEIPTS_NOT_CONFIGURED")


def receipt_message(provider_id: str, key_id: str, digest_sha256: str, anchored_at_utc: datetime) -> bytes:
    stamp = anchored_at_utc.astimezone(timezone.utc).isoformat()
    return f"FB-CUSTODY-V1|{provider_id}|{key_id}|{digest_sha256}|{stamp}".encode("utf-8")


def _signature_ok(receipt: Receipt, roots: Mapping[tuple[str, str], TrustRoot]) -> bool:
    root = roots.get((receipt.provider_id, receipt.key_id))
    if root is None:
        return False  # unknown signer, including any self-signed local key
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:  # no crypto backend: nothing can be verified, so nothing is verified
        return False
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(root.public_key_b64, validate=True))
        key.verify(base64.b64decode(receipt.signature_b64, validate=True),
                   receipt_message(receipt.provider_id, receipt.key_id, receipt.digest_sha256,
                                   receipt.anchored_at_utc))
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True


def classify_custody(
    *,
    manifest_sha256: str,
    decision_at_utc: datetime,
    entry_earliest_at_utc: datetime,
    receipt: Receipt | None,
    trust_roots: Mapping[tuple[str, str], TrustRoot],
) -> CustodyStatus:
    for stamp in (decision_at_utc, entry_earliest_at_utc):
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("decision and entry times must be timezone-aware")
    if not _SHA.match(manifest_sha256):
        raise ValueError("manifest digest must be sha256 hex")
    if decision_at_utc >= entry_earliest_at_utc:
        return CustodyStatus.CONFLICTED  # decided at or after the actionable entry time (new strategy after entry)
    if receipt is None:
        return CustodyStatus.LOCAL_ONLY
    if receipt.digest_sha256 != manifest_sha256:
        return CustodyStatus.CONFLICTED  # altered payload, or a receipt replayed from another manifest
    anchored = receipt.anchored_at_utc
    if anchored.tzinfo is None or anchored.utcoffset() is None:
        return CustodyStatus.CONFLICTED
    if anchored < decision_at_utc:
        return CustodyStatus.CONFLICTED  # a receipt cannot precede the decision it anchors (false clock)
    if anchored >= entry_earliest_at_utc:
        return CustodyStatus.LATE
    if not _signature_ok(receipt, trust_roots):
        return CustodyStatus.UNVERIFIED
    return CustodyStatus.VERIFIED


def formal_claim_eligible(status: CustodyStatus) -> bool:
    return status is CustodyStatus.VERIFIED
