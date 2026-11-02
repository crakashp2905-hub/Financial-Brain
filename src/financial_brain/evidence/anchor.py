"""Provider-neutral external evidence-anchor contract.

Implementations may call an approved custody provider, but a local/mock signer is
never treated as independent verification. Network failure must fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


@dataclass(frozen=True)
class EvidenceAnchor:
    provider: str
    digest_sha256: str
    anchored_at_utc: datetime
    receipt: str

    def validate(self) -> None:
        import re
        if not self.provider or not self.receipt or not re.fullmatch(r"[0-9a-f]{64}", self.digest_sha256):
            raise ValueError("invalid evidence anchor")
        if self.anchored_at_utc.tzinfo is None or self.anchored_at_utc.utcoffset() is None:
            raise ValueError("anchor timestamp must be timezone-aware")
        if self.anchored_at_utc.astimezone(timezone.utc) > datetime.now(timezone.utc):
            raise ValueError("anchor timestamp is in the future")


class AnchorProvider(Protocol):
    def anchor(self, digest_sha256: str) -> EvidenceAnchor:
        """Return a provider receipt or raise; never synthesize a receipt offline."""

    def verify(self, anchor: EvidenceAnchor) -> bool:
        """Verify against the provider trust root; failure returns False or raises."""
