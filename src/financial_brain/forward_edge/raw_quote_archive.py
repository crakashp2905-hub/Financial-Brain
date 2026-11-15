"""Locally archive market events only under an explicit, source-scoped retention attestation."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.market_data import DataError, NormalizedEvent


class RawArchiveError(ValueError):
    """Raw quote retention is not authorized or the canonical payload is invalid."""


class RawQuoteArchive:
    """Append-only hash-chained storage for raw quotes and scoped normalized market events.

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
        authorization = {key: retention_attestation[key] for key in required}
        authorization["approved"] = True
        for scope_key in ("permitted_event_kinds", "permitted_sources", "permitted_actions"):
            if scope_key in retention_attestation:
                values = retention_attestation[scope_key]
                if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip()
                                                       for value in values):
                    raise RawArchiveError(f"{scope_key} must be a list of nonempty strings")
                authorization[scope_key] = list(values)
        self.path = Path(path)
        self.journal = DurableJournal(self.path)
        self.authorization = authorization

    def require_permission(self, *, action: str, event_kind: str, source: str) -> None:
        """Fail closed unless the attestation names this exact use, kind, and source."""
        actions = self.authorization.get("permitted_actions")
        kinds = self.authorization.get("permitted_event_kinds")
        sources = self.authorization.get("permitted_sources")
        if not isinstance(actions, list) or action not in actions:
            raise RawArchiveError(f"retention attestation does not permit {action}")
        if (not isinstance(kinds, list) or not kinds
                or (event_kind != "*" and event_kind not in kinds)):
            raise RawArchiveError(f"retention attestation does not permit {event_kind} use")
        if (not isinstance(sources, list) or not sources
                or (source != "*" and source not in sources)):
            raise RawArchiveError(f"retention attestation does not permit source {source!r}")

    def append_quote(self, instrument: str, quote: dict, *, received_at: datetime,
                     available_at: datetime) -> dict:
        self.require_permission(action="persist", event_kind="QUOTE", source="kite_connect")
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

    def append_market_event(self, event: NormalizedEvent, *, source: str) -> dict:
        """Persist a normalized quote, depth, trade tick, or bar when rights are scoped.

        Unlike the legacy quote-only capture, this method requires the operator's
        retention attestation to name both the permitted event kind and source. The
        event's source, receipt, and availability clocks are preserved verbatim in
        UTC; historical data becomes available to a replay only at its actual local
        retrieval/receipt time, never retroactively at its event timestamp.
        """
        if not isinstance(event, NormalizedEvent):
            raise RawArchiveError("a validated normalized market event is required")
        if not event.instrument.strip():
            raise RawArchiveError("normalized market event instrument is required")
        if event.kind not in {"QUOTE", "DEPTH", "TICK", "BAR"}:
            raise RawArchiveError("only quote, depth, tick, and bar events may be archived here")
        if not isinstance(source, str) or not source.strip():
            raise RawArchiveError("market event source must be named")
        self.require_permission(action="persist", event_kind=event.kind, source=source)
        payload = dict(event.payload)
        if len(payload) != len(event.payload):
            raise RawArchiveError("normalized market event payload keys must be unique")
        try:
            payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise RawArchiveError("normalized event payload must be finite canonical JSON") from exc
        row = {
            "t": "MARKET_EVENT_CAPTURE",
            "archive_seq": self.journal.seq + 1,
            "source": source,
            "kind": event.kind,
            "instrument": event.instrument,
            "event_utc": event.event_utc.astimezone(timezone.utc).isoformat(),
            "received_utc": event.received_utc.astimezone(timezone.utc).isoformat(),
            "available_utc": event.available_utc.astimezone(timezone.utc).isoformat(),
            "ingested_utc": event.ingested_utc.astimezone(timezone.utc).isoformat()
            if event.ingested_utc is not None else None,
            "payload_json": payload_json,
            "payload_sha256": hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
            "event_sha256": event.content_sha256(),
            "retention_authorization_id": self.authorization["authorization_id"],
            "retention_evidence_sha256": self.authorization["evidence_sha256"],
        }
        sequence = self.journal.append(row)
        return {"archive_seq": sequence, "event_sha256": row["event_sha256"],
                "archive_chain_sha256": self.journal.head,
                "archive_id": f"{sequence}:{row['event_sha256']}"}

    def records(self) -> list[dict]:
        self.require_permission(action="local_read", event_kind="*", source="*")
        rows = self.journal.records()
        self._require_record_scopes(rows, action="local_read")
        return rows

    def export_records(self) -> list[dict]:
        """Expose archived payloads to another system only under explicit redistribution scope."""
        self.require_permission(action="redistribute", event_kind="*", source="*")
        rows = self.journal.records()
        self._require_record_scopes(rows, action="redistribute")
        return rows

    def replay_events(self, *, as_of: datetime | None = None) -> list[NormalizedEvent]:
        """Reconstruct captured normalized events, optionally bounded by availability time."""
        self.require_permission(action="replay", event_kind="*", source="*")
        if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
            raise RawArchiveError("replay as_of must be timezone-aware")
        cutoff = as_of.astimezone(timezone.utc) if as_of is not None else None
        events: list[tuple[int, NormalizedEvent]] = []
        rows = self.journal.records()
        self._require_record_scopes(rows, action="replay", normalized_only=True)
        for row in rows:
            if row.get("t") != "MARKET_EVENT_CAPTURE":
                continue
            event = _event_from_record(row)
            if cutoff is None or event.available_utc <= cutoff:
                events.append((int(row["archive_seq"]), event))
        return [event for _, event in sorted(events, key=lambda pair: (pair[1].available_utc, pair[0]))]

    def _require_record_scopes(self, rows: list[dict], *, action: str,
                               normalized_only: bool = False) -> None:
        for row in rows:
            record_type = row.get("t")
            if normalized_only and record_type != "MARKET_EVENT_CAPTURE":
                continue
            if record_type == "MARKET_EVENT_CAPTURE":
                kind, source = row.get("kind"), row.get("source")
            elif record_type == "RAW_QUOTE_CAPTURE":
                kind, source = "QUOTE", row.get("source")
            else:
                raise RawArchiveError("archive contains an unclassified record whose rights cannot be inferred")
            self.require_permission(action=action, event_kind=str(kind), source=str(source))

    def verify(self) -> bool:
        verified = DurableJournal(self.path)
        for row in verified.records():
            payload = row.get("payload_json")
            if row.get("t") in {"RAW_QUOTE_CAPTURE", "MARKET_EVENT_CAPTURE"}:
                if not isinstance(payload, str) or hashlib.sha256(payload.encode("utf-8")).hexdigest() != row.get(
                        "payload_sha256"):
                    return False
            if row.get("t") == "MARKET_EVENT_CAPTURE":
                try:
                    if _event_from_record(row).content_sha256() != row.get("event_sha256"):
                        return False
                except (KeyError, TypeError, ValueError, DataError):
                    return False
        return True


def _event_from_record(row: dict) -> NormalizedEvent:
    payload = json.loads(row["payload_json"])
    if not isinstance(payload, dict):
        raise RawArchiveError("archived normalized event payload is not an object")
    return NormalizedEvent(
        kind=str(row["kind"]),
        instrument=str(row["instrument"]),
        event_utc=datetime.fromisoformat(row["event_utc"]),
        received_utc=datetime.fromisoformat(row["received_utc"]),
        available_utc=datetime.fromisoformat(row["available_utc"]),
        payload=tuple(sorted((str(key), str(value)) for key, value in payload.items())),
        ingested_utc=datetime.fromisoformat(row["ingested_utc"]) if row.get("ingested_utc") else None,
    )
