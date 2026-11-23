"""PA-02-15: future-published source rows cannot change point-in-time decisions."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json

from financial_brain.forward_edge.execution import intent_from_signal
from financial_brain.forward_edge.market_data import feature_at
from financial_brain.forward_edge.market_events import EventKind, MarketEvent
from financial_brain.forward_edge.quote_archive import QuoteArchive

T0 = datetime(2026, 11, 23, 4, 0, tzinfo=timezone.utc)
DECISION = T0 + timedelta(minutes=5)


def _bar(index: int, close: float, *, available_at: datetime | None = None,
         event_id: str | None = None, event_at: datetime | None = None) -> MarketEvent:
    event_at = event_at or T0 + timedelta(minutes=index)
    available = available_at or event_at
    return MarketEvent(
        event_id=event_id or f"bar-{index}-{close}", kind=EventKind.BAR, venue="NSE",
        instrument_id="NSE:INFY", event_time=event_at, received_at=available,
        available_at=available,
        payload={"open": close, "high": close, "low": close, "close": close, "volume": 100},
        source="synthetic-fixture", sequence=index, evidence_id="pa-02-15-fixture",
        published_at=available,
    )


def _decision_bytes(archive: QuoteArchive) -> bytes:
    snapshot = archive.snapshot(
        decision_id="pa-02-15", venue="NSE", instrument_id="NSE:INFY", decision_time=DECISION,
    )
    events = [archive.get(row["event_id"]) for row in snapshot["events"]]
    bars = [event for event in events if event.kind == EventKind.BAR]
    closes = [float(event.payload["close"]) for event in bars]
    timestamps = [event.event_time for event in bars]
    feature = feature_at(closes, timestamps, DECISION, window=3)
    assert feature is not None
    side = "BUY" if feature >= 103 else "SELL"
    intended = intent_from_signal(
        {"instrument": "NSE:INFY", "side": side, "quantity": 10,
         "decision_utc": DECISION, "limit": feature},
        tick=Decimal("0.05"),
    )
    return json.dumps(
        {"features": {"sma3": feature}, "intended_order": asdict(intended)},
        sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")


def test_mutating_future_published_rows_keeps_feature_and_intended_order_bytes_identical(tmp_path):
    baseline = QuoteArchive(tmp_path / "baseline.sqlite3")
    mutated = QuoteArchive(tmp_path / "mutated.sqlite3")
    try:
        for index, close in enumerate((100, 101, 102, 103, 104), start=1):
            event = _bar(index, close)
            baseline.append(event, ingested_at=event.available_at)
            mutated.append(event, ingested_at=event.available_at)

        published_late = DECISION + timedelta(seconds=1)
        baseline.append(_bar(4, 105, available_at=published_late, event_id="late-4",
                             event_at=T0 + timedelta(minutes=4)), ingested_at=published_late)
        baseline.append(_bar(5, 106, available_at=published_late, event_id="late-5",
                             event_at=DECISION), ingested_at=published_late)
        # The exchange-time prefix looks historical, but these revised values were not
        # published or available until after the decision and must remain excluded.
        mutated.append(_bar(4, 1_000_000, available_at=published_late, event_id="late-4",
                            event_at=T0 + timedelta(minutes=4)), ingested_at=published_late)
        mutated.append(_bar(5, 0.01, available_at=published_late, event_id="late-5",
                            event_at=DECISION), ingested_at=published_late)

        assert _decision_bytes(baseline) == _decision_bytes(mutated)
    finally:
        baseline.close()
        mutated.close()
