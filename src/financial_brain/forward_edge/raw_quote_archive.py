"""Locally archive canonical Kite quote objects under an explicit retention attestation."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from financial_brain.forward_edge.durable import DurableJournal


class RawArchiveError(ValueError):
    """Raw quote retention is not authorized or the canonical payload is invalid."""


class RawQuoteArchive:
    """Append-only hash-chained storage for parsed Kite quote objects.

    The gate proves only that the caller supplied an attestation record. It cannot
    establish the legal scope or correctness of that operator-supplied statement.
    """

    def __init__(self, path: Path, *, retention_attestation: dict) -> None:
        required = ("authorization_id", "controller", "purpose", "evidence_sha256", "approved_at_utc")
        if not isinstance(retention_attestation, dict) or retention_attestation.get("approved") is not True:
            raise RawArchiveError("raw quote archive needs an explicit approved retention attestation")
        if any(not isinstance(retention_attestation.get(key), str)
               or not retention_attestation[key].strip() for key in required):
            raise RawArchiveError("retention attestation is missing a required identity or scope field")
        digest = retention_attestation["evidence_sha256"]
        try:
            if len(digest) != 64 or len(bytes.fromhex(digest)) != 32:
                raise ValueError
        except ValueError as exc:
            raise RawArchiveError("retention evidence must be identified by a SHA-256 digest") from exc
        try:
            approved_at = datetime.fromisoformat(retention_attestation["approved_at_utc"])
            if approved_at.tzinfo is None or approved_at.utcoffset() is None:
                raise ValueError
        except ValueError as exc:
            raise RawArchiveError("retention approval timestamp must be timezone-aware") from exc
        self.path = Path(path)
        self.journal = DurableJournal(self.path)
        self.authorization = {key: retention_attestation[key] for key in required}
        self.authorization["approved"] = True

    def append_quote(self, instrument: str, quote: dict, *, received_at: datetime,
                     available_at: datetime) -> dict:
        if not instrument or not isinstance(quote, dict):
            raise RawArchiveError("instrument and parsed Kite quote object are required")
        if received_at.tzinfo is None or received_at.utcoffset() is None \
                or available_at.tzinfo is None or available_at.utcoffset() is None:
            raise RawArchiveError("quote receive and availability clocks must be timezone-aware")
        received = received_at.astimezone(timezone.utc)
        available = available_at.astimezone(timezone.utc)
        if available < received:
            raise RawArchiveError("quote availability cannot precede receipt")
        try:
            payload = json.dumps(quote, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=False, default=str, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise RawArchiveError("Kite quote object must be finite canonical JSON") from exc
        payload_digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        row = {
            "t": "RAW_QUOTE_CAPTURE",
            "source": "kite_connect",
            "instrument": instrument,
            "received_at_utc": received.isoformat(),
            "available_at_utc": available.isoformat(),
            "payload_json": payload,
            "payload_sha256": payload_digest,
            "retention_authorization_id": self.authorization["authorization_id"],
            "retention_evidence_sha256": self.authorization["evidence_sha256"],
        }
        sequence = self.journal.append(row)
        return {"archive_seq": sequence, "payload_sha256": payload_digest,
                "archive_chain_sha256": self.journal.head}

    def records(self) -> list[dict]:
        return self.journal.records()

    def verify(self) -> bool:
        verified = DurableJournal(self.path)
        for row in verified.records():
            if row.get("t") != "RAW_QUOTE_CAPTURE":
                continue
            payload = row.get("payload_json")
            if not isinstance(payload, str) or hashlib.sha256(payload.encode("utf-8")).hexdigest() != row.get(
                    "payload_sha256"):
                return False
        return True
