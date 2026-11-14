"""Causal, content-addressed sealing for forward-session signals and abstentions."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from financial_brain.forward_edge.market_data import NormalizedEvent


class SignalSealError(ValueError):
    """A signal cannot be sealed against the supplied decision-time inputs."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SignalSealError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def seal_decision(*, session_id: str, strategy_sha256: str, config_sha256: str,
                  decision_at: datetime, decision: dict, inputs: list[NormalizedEvent],
                  preflight: dict, feed_health_sha256: str | None) -> dict:
    """Build an immutable seal over the output and all decision-visible source event hashes.

    This function contains no outcome input. The runner persists its return value
    in the same durable record as the signal before risk, execution, and outcome.
    """
    visible = _utc(decision_at, "decision time")
    if not isinstance(session_id, str) or not session_id.strip():
        raise SignalSealError("session ID is required")
    for name, digest in (("strategy", strategy_sha256), ("config", config_sha256)):
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise SignalSealError(f"{name} identity must be a SHA-256 digest")
    if not isinstance(decision, dict) or decision.get("side") not in {"BUY", "SELL", "NONE", "NO_TRADE"}:
        raise SignalSealError("decision must contain an actionable signal or explicit abstention")
    try:
        decision_body = json.dumps(decision, sort_keys=True, separators=(",", ":"), allow_nan=False)
        preflight_body = json.dumps(preflight, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SignalSealError("decision and preflight evidence must be finite JSON") from exc
    if feed_health_sha256 is not None and not _SHA256.fullmatch(feed_health_sha256):
        raise SignalSealError("feed-health identity must be a SHA-256 digest")

    event_inputs = []
    for event in inputs:
        available = _utc(event.available_utc, "input availability")
        if available > visible:
            raise SignalSealError("a future or not-yet-available event cannot be sealed into a signal")
        event_inputs.append({"event_sha256": event.content_sha256(),
                             "available_at_utc": available.isoformat(),
                             "instrument": event.instrument, "kind": event.kind})
    event_inputs.sort(key=lambda row: (row["available_at_utc"], row["event_sha256"]))

    signal = dict(decision)
    side = signal["side"]
    if side in {"NONE", "NO_TRADE"}:
        abstention = signal.get("reason") or signal.get("abstention_reason") or "ABSTAINED_WITHOUT_REASON"
        signal["abstention_reason"] = str(abstention)

    input_manifest = {
        "session_id": session_id,
        "strategy_sha256": strategy_sha256,
        "config_sha256": config_sha256,
        "decision_at_utc": visible.isoformat(),
        "event_inputs": event_inputs,
        "preflight_sha256": hashlib.sha256(preflight_body.encode()).hexdigest(),
        "feed_health_sha256": feed_health_sha256,
    }
    input_manifest_sha256 = hashlib.sha256(
        json.dumps(input_manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    signal_body = {
        "decision": signal,
        "decision_sha256": hashlib.sha256(decision_body.encode()).hexdigest(),
        "input_manifest_sha256": input_manifest_sha256,
        "sealed_at_utc": visible.isoformat(),
        "timestamp_source": "SESSION_DECISION_CLOCK",
    }
    signal_sha256 = hashlib.sha256(
        json.dumps(signal_body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return {
        "decision": signal,
        "strategy_sha256": strategy_sha256,
        "config_sha256": config_sha256,
        "decision_at_utc": visible.isoformat(),
        "sealed_at_utc": visible.isoformat(),
        "timestamp_source": "SESSION_DECISION_CLOCK",
        "decision_sha256": signal_body["decision_sha256"],
        "input_event_sha256s": [row["event_sha256"] for row in event_inputs],
        "input_events": event_inputs,
        "preflight_sha256": input_manifest["preflight_sha256"],
        "feed_health_sha256": feed_health_sha256,
        "input_manifest_sha256": input_manifest_sha256,
        "signal_sha256": signal_sha256,
    }
