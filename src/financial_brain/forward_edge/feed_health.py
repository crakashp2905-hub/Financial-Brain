"""Observable health counters for polling feeds; REST snapshots do not imply packet sequence."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any


class FeedHealthError(ValueError):
    """Invalid feed-health clock or polling configuration."""


class FeedHealthMonitor:
    """Track poll attempts, recoveries, coverage and repeated snapshots without inventing packet loss."""

    def __init__(self, instruments: tuple[str, ...], *, poll_interval_seconds: float,
                 stale_after_seconds: float) -> None:
        if (not instruments or len(set(instruments)) != len(instruments)
                or any(not isinstance(value, str) or not value.strip() for value in instruments)):
            raise FeedHealthError("feed health requires unique non-empty instrument IDs")
        if (not math.isfinite(poll_interval_seconds) or poll_interval_seconds <= 0
                or not math.isfinite(stale_after_seconds) or stale_after_seconds <= 0):
            raise FeedHealthError("poll and stale durations must be finite and positive")
        self.instruments = frozenset(instruments)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.stale_after_seconds = float(stale_after_seconds)
        self.poll_attempts = 0
        self.successful_polls = 0
        self.failed_polls = 0
        self.reconnects = 0
        self.consecutive_failures = 0
        self.missing_quote_count = 0
        self.missing_quote_polls = 0
        self.unchanged_snapshot_count = 0
        self.missed_poll_slots = 0
        self.first_attempt_at: datetime | None = None
        self.last_attempt_at: datetime | None = None
        self.last_success_at: datetime | None = None
        self.last_failure_class: str | None = None
        self._last_payload_hash: dict[str, str] = {}

    @staticmethod
    def _utc(value: datetime, label: str) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise FeedHealthError(f"{label} must be timezone-aware")
        return value.astimezone(timezone.utc)

    def record_attempt(self, at: datetime) -> None:
        self.last_attempt_at = self._utc(at, "poll attempt time")
        if self.first_attempt_at is None:
            self.first_attempt_at = self.last_attempt_at
        self.poll_attempts += 1

    def record_failure(self, at: datetime, error: BaseException | str) -> None:
        failed_at = self._utc(at, "poll failure time")
        self.failed_polls += 1
        self.consecutive_failures += 1
        self.last_attempt_at = failed_at
        if self.first_attempt_at is None:
            self.first_attempt_at = failed_at
        self.last_failure_class = type(error).__name__ if isinstance(error, BaseException) else str(error)

    def record_success(self, at: datetime, quotes: dict[str, Any]) -> None:
        received = self._utc(at, "successful poll time")
        if self.last_success_at is not None:
            elapsed = (received - self.last_success_at).total_seconds()
            if elapsed < 0:
                raise FeedHealthError("successful poll clock moved backwards")
            self.missed_poll_slots += max(0, math.ceil(elapsed / self.poll_interval_seconds) - 1)
        if self.consecutive_failures:
            self.reconnects += 1
        self.successful_polls += 1
        self.consecutive_failures = 0
        self.last_success_at = received
        self.last_attempt_at = received
        present = self.instruments.intersection(quotes)
        missing = len(self.instruments - present)
        self.missing_quote_count += missing
        if missing:
            self.missing_quote_polls += 1
        for instrument in present:
            canonical = json.dumps(quotes[instrument], sort_keys=True, separators=(",", ":"),
                                   default=str, allow_nan=True)
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if self._last_payload_hash.get(instrument) == digest:
                self.unchanged_snapshot_count += 1
            self._last_payload_hash[instrument] = digest

    def snapshot(self, *, now: datetime | None = None,
                 unavailable_reason: str | None = None) -> dict[str, Any]:
        observed_now = self._utc(now or datetime.now(timezone.utc), "health snapshot time")
        if unavailable_reason not in {None, "MARKET_CLOSED", "UNAUTHORIZED", "PROVIDER_OUTAGE"}:
            raise FeedHealthError("unsupported market-data unavailability reason")
        reference = self.last_success_at or self.first_attempt_at
        silence = (observed_now - reference).total_seconds() if reference is not None else None
        if silence is not None and silence < 0:
            raise FeedHealthError("health snapshot clock precedes latest poll")
        stale = silence is not None and silence >= self.stale_after_seconds
        if unavailable_reason is not None:
            status = "NO_DATA"
        elif stale:
            status = "STALE"
        elif self.successful_polls == 0:
            status = "NO_DATA"
        elif self.missing_quote_polls:
            status = "DEGRADED"
        else:
            status = "HEALTHY"
        return {
            "provider": "KITE_CONNECT_READ_ONLY_POLL",
            "status": status,
            "decision_status": "NO_TRADE" if status in {"NO_DATA", "STALE"} else None,
            "unavailable_reason": unavailable_reason,
            "poll_attempts": self.poll_attempts,
            "successful_polls": self.successful_polls,
            "failed_polls": self.failed_polls,
            "reconnects": self.reconnects,
            "consecutive_failures": self.consecutive_failures,
            "missing_quote_count": self.missing_quote_count,
            "missing_quote_polls": self.missing_quote_polls,
            "unchanged_snapshot_count": self.unchanged_snapshot_count,
            "missed_poll_slots": self.missed_poll_slots,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_failure_class": self.last_failure_class,
            "silence_seconds": silence,
            "dropped_packet_count": None,
            "dropped_packet_count_reason": "REST polling has no packet sequence; missed poll slots are reported separately",
        }
