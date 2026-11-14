"""Immutable source-capture start receipts for forward paper sessions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.market_data import FeedHealth, entry_allowed
from financial_brain.forward_edge.session_runner import ForwardRunConfig, ForwardSessionRunner, RunnerError


class CaptureStartError(ValueError):
    """Invalid source-capture start or conflicting session receipt."""


def _utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CaptureStartError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class SourceEntitlement:
    source_id: str
    provider: str
    enabled: bool
    evidence_id: str
    valid_until: datetime

    def canonical(self, *, at: datetime) -> dict:
        valid_until = _utc(self.valid_until, "entitlement valid_until")
        return {
            "source_id": self.source_id,
            "provider": self.provider,
            "enabled": self.enabled,
            "evidence_id": self.evidence_id,
            "valid_until_utc": valid_until.isoformat(),
            "valid": (bool(self.source_id.strip()) and bool(self.provider.strip())
                      and type(self.enabled) is bool and self.enabled
                      and bool(self.evidence_id.strip()) and valid_until > at),
        }


@dataclass(frozen=True)
class CaptureStartReceipt:
    capture_id: str
    capture_started_at: datetime
    earliest_decision_at: datetime
    clock_skew_ms: float
    clock_evidence_id: str
    quote_health: FeedHealth
    entitlements: tuple[SourceEntitlement, ...]


def record_capture_start(runner: ForwardSessionRunner, config: ForwardRunConfig,
                         receipt: CaptureStartReceipt) -> dict:
    """Persist capture, timing, feed-health and entitlement evidence before any source events.

    The source adapter must start its capture and provide a unique capture receipt before
    this function is called. This function then durably journals that receipt ahead of
    the first normalized market event and rejects starts at or after a decision deadline.
    """
    started = _utc(receipt.capture_started_at, "capture_started_at")
    decision = _utc(receipt.earliest_decision_at, "earliest_decision_at")
    if not isinstance(receipt.capture_id, str) or not receipt.capture_id.strip():
        raise CaptureStartError("capture ID is required")
    if started >= decision:
        raise CaptureStartError("source capture must start before the earliest strategy decision")
    if (type(receipt.clock_skew_ms) not in (int, float) or not math.isfinite(receipt.clock_skew_ms)
            or not isinstance(receipt.clock_evidence_id, str) or not receipt.clock_evidence_id.strip()):
        raise CaptureStartError("finite clock skew and clock evidence are required")
    if not isinstance(receipt.quote_health, FeedHealth):
        raise CaptureStartError("capture-start quote health is required")
    entitlements = tuple(receipt.entitlements)
    if any(not isinstance(item, SourceEntitlement) for item in entitlements):
        raise CaptureStartError("source entitlement records must be typed")
    source_ids = [item.source_id for item in entitlements]
    if len(source_ids) != len(set(source_ids)):
        raise CaptureStartError("source entitlement IDs must be unique")
    entitlement_rows = [item.canonical(at=started) for item in sorted(entitlements, key=lambda item: item.source_id)]
    quote_ok, quote_reasons = entry_allowed(receipt.quote_health, started)
    quote_facts = {
        "last_available_utc": (_utc(receipt.quote_health.last_available_utc, "last quote").isoformat()
                               if receipt.quote_health.last_available_utc else None),
        "last_heartbeat_utc": (_utc(receipt.quote_health.last_heartbeat_utc, "last heartbeat").isoformat()
                               if receipt.quote_health.last_heartbeat_utc else None),
        "gaps": receipt.quote_health.gaps,
        "disconnects": receipt.quote_health.disconnects,
        "dropped_updates": receipt.quote_health.dropped_updates,
        "duplicates": receipt.quote_health.duplicates,
        "reconnects": receipt.quote_health.reconnects,
        "healthy_at_capture_start": quote_ok,
        "reasons": quote_reasons,
    }
    valid_entitlements = bool(entitlement_rows) and all(item["valid"] for item in entitlement_rows)
    record = {
        "t": "SOURCE_CAPTURE_START",
        "session_id": config.session_id,
        "capture_id": receipt.capture_id,
        "capture_started_at_utc": started.isoformat(),
        "earliest_decision_at_utc": decision.isoformat(),
        "lead_ms": (decision - started).total_seconds() * 1000,
        "clock_skew_ms": receipt.clock_skew_ms,
        "clock_evidence_id": receipt.clock_evidence_id,
        "quote_health": quote_facts,
        "source_entitlements": entitlement_rows,
        "entry_allowed_by_entitlement": valid_entitlements,
        "entitlement_blockers": [] if valid_entitlements else [
            "SOURCE_ENTITLEMENT_MISSING_OR_INVALID"
        ],
    }
    digest = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()
    record["receipt_sha256"] = digest

    runner.begin(config)
    journal = DurableJournal(runner.journal_path)
    rows = [row for row in journal.records() if row.get("session_id") == config.session_id]
    if any(row.get("t") in {"MARKET_EVENT", "DECISION", "SESSION_CLOSED"} for row in rows):
        raise RunnerError("capture-start receipt must be recorded before market events and decisions")
    prior = next((row for row in rows if row.get("t") == "SOURCE_CAPTURE_START"), None)
    if prior is not None:
        if prior != record:
            raise RunnerError("source-capture start receipt is immutable")
        return prior
    journal.append(record)
    return record
