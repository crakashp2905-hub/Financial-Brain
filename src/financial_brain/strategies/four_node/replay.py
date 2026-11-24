"""Point-in-time, timestamp-preserving Four-Node event replay (FB-2534)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Sequence

from financial_brain.strategies.four_node.nodes import pivots
from financial_brain.strategies.four_node.stream import (
    BreakoutCandidate, FourNodeStateMachine, StreamEvent, StreamResult,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ReplayEligibility:
    """Effective-dated, source-attested instrument eligibility visible at a bar's timestamp."""

    instrument_id: str
    valid_from: datetime
    available_at: datetime
    source_sha256: str
    tradable: bool
    valid_until: datetime | None = None

    def __post_init__(self) -> None:
        if not self.instrument_id.strip():
            raise ValueError("eligibility instrument ID is required")
        clocks = (self.valid_from, self.available_at) + ((self.valid_until,) if self.valid_until else ())
        if any(value.tzinfo is None or value.utcoffset() is None for value in clocks):
            raise ValueError("eligibility timestamps must be timezone-aware")
        if self.valid_until is not None and self.valid_until <= self.valid_from:
            raise ValueError("eligibility validity interval is empty")
        if not _SHA256.fullmatch(self.source_sha256):
            raise ValueError("eligibility source must be a lowercase SHA-256")
        if type(self.tradable) is not bool:
            raise ValueError("tradable must be an explicit boolean")

    def permits(self, instrument_id: str, at: datetime, *, available_at: datetime | None = None) -> bool:
        known_at = available_at or at
        return (
            self.tradable
            and instrument_id == self.instrument_id
            and self.valid_from <= at
            and (self.valid_until is None or at < self.valid_until)
            and self.available_at <= known_at
        )


@dataclass(frozen=True)
class ReplayBar:
    """One original, immutable OHLC observation and its point-in-time eligibility evidence."""

    event_id: str
    instrument_id: str
    session_id: str
    bar_index: int
    observed_at: datetime
    open: float
    high: float
    low: float
    close: float
    source_sha256: str
    eligibility: ReplayEligibility
    available_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.event_id.strip() or not self.instrument_id.strip() or not self.session_id.strip():
            raise ValueError("replay bar event, instrument and session IDs are required")
        if type(self.bar_index) is not int or self.bar_index < 0:
            raise ValueError("replay bar index must be a nonnegative integer")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("replay bar timestamp must be timezone-aware")
        available_at = self.observed_at if self.available_at is None else self.available_at
        if available_at.tzinfo is None or available_at.utcoffset() is None or available_at < self.observed_at:
            raise ValueError("bar availability timestamp must be aware and no earlier than source time")
        object.__setattr__(self, "available_at", available_at)
        values = (self.open, self.high, self.low, self.close)
        if any(isinstance(value, bool) or not isinstance(value, (int, float))
               or not float("-inf") < value < float("inf")
               or value <= 0 for value in values):
            raise ValueError("OHLC prices must be finite and positive")
        if self.high < max(self.open, self.close, self.low) or self.low > min(self.open, self.close):
            raise ValueError("replay OHLC bounds are inconsistent")
        if not _SHA256.fullmatch(self.source_sha256):
            raise ValueError("bar source must be a lowercase SHA-256")
        if self.eligibility.instrument_id != self.instrument_id:
            raise ValueError("instrument eligibility identity does not match bar")


@dataclass(frozen=True)
class ReplayComparison:
    events: tuple[StreamEvent, ...]
    streaming_results: tuple[StreamResult, ...]
    historical_results: tuple[StreamResult, ...]
    warmed_bars: int

    @property
    def matches(self) -> bool:
        return self.streaming_results == self.historical_results

    @property
    def candidates(self) -> tuple[BreakoutCandidate, ...]:
        return tuple(candidate for result in self.historical_results for candidate in result.candidates)


def compare_stream_and_replay(
    bars: Sequence[ReplayBar],
    *,
    left: int = 2,
    right: int = 2,
    warmup_bars: int = 200,
    max_age_bars: int = 120,
    expected_interval: timedelta | None = timedelta(minutes=1),
    tie_policy: str = "STRICT",
) -> ReplayComparison:
    """Run the same timestamped observations online and as a historical event replay.

    Pivots are recomputed from the prefix available at each close. A pivot is emitted only after
    its right-side confirmation bars have closed. Ineligible intervals clear pattern state, gaps
    fail closed, and warm-up bars update pattern context without emitting breakout decisions.
    """
    if left < 1 or right < 1 or warmup_bars < 1 or max_age_bars < 1:
        raise ValueError("pivot windows, warm-up and maximum age must be positive")
    if expected_interval is not None and expected_interval <= timedelta(0):
        raise ValueError("expected bar interval must be positive")
    if tie_policy not in {"STRICT", "LEFTMOST", "RIGHTMOST"}:
        raise ValueError("unsupported fractal tie policy")
    if not bars:
        return ReplayComparison((), (), (), 0)

    event_ids: set[str] = set()
    sessions: set[str] = set()
    previous: ReplayBar | None = None
    session_bars: list[ReplayBar] = []
    eligible_bars = 0
    events: list[StreamEvent] = []
    for bar in bars:
        if bar.event_id in event_ids:
            raise ValueError("duplicate replay bar event ID")
        event_ids.add(bar.event_id)
        if previous is not None:
            if bar.instrument_id != previous.instrument_id:
                raise ValueError("one replay cohort must contain a single instrument")
            previous_available_at = previous.available_at or previous.observed_at
            bar_available_at = bar.available_at or bar.observed_at
            if bar.observed_at <= previous.observed_at or bar_available_at <= previous_available_at:
                raise ValueError("replay bars must have strictly increasing original timestamps")
            if bar.session_id == previous.session_id:
                if bar.bar_index != previous.bar_index + 1:
                    raise ValueError("replay bar indices must be contiguous within a session")
                if expected_interval is not None and bar.observed_at - previous.observed_at != expected_interval:
                    raise ValueError("missing or irregular bar interval; replay refused")
            else:
                if bar.session_id in sessions or bar.bar_index != 0:
                    raise ValueError("sessions must be unique and begin at bar index zero")
                sessions.add(bar.session_id)
                session_bars = []
        else:
            if bar.bar_index != 0:
                raise ValueError("first session must begin at bar index zero")
            sessions.add(bar.session_id)
        allowed = bar.eligibility.permits(
            bar.instrument_id, bar.observed_at, available_at=bar.available_at
        )
        if not allowed:
            events.append(StreamEvent(
                f"eligibility-reset:{bar.event_id}", "SESSION_RESET", bar.session_id,
                bar.bar_index, observed_at=bar.observed_at, available_at=bar.available_at,
                source_sha256=bar.eligibility.source_sha256,
            ))
            session_bars.append(bar)
            previous = bar
            continue

        eligible_bars += 1
        session_bars.append(bar)
        warmed = eligible_bars >= warmup_bars
        if warmed:
            events.append(StreamEvent(
                f"bar:{bar.event_id}", "BAR_CLOSED", bar.session_id, bar.bar_index,
                close=bar.close, observed_at=bar.observed_at, available_at=bar.available_at,
                source_sha256=bar.source_sha256,
            ))
        if len(session_bars) >= left + right + 1:
            highs = [row.high for row in session_bars]
            lows = [row.low for row in session_bars]
            ids = [row.event_id for row in session_bars]
            newly_confirmed = [node for node in pivots(
                highs, lows, left, right, tie_policy=tie_policy, bar_ids=ids,
                source_sha256s=[row.source_sha256 for row in session_bars],
            ) if node.confirmed_at == bar.bar_index]
            for node in newly_confirmed:
                window = session_bars[node.pivot_at - left:node.pivot_at + right + 1]
                if len(window) != left + right + 1 or any(
                        not row.eligibility.permits(row.instrument_id, row.observed_at,
                                                   available_at=row.available_at) for row in window):
                    continue
                events.append(StreamEvent(
                    f"node:{node.node_id}", "NODE_CONFIRMED", bar.session_id, bar.bar_index,
                    node=node, observed_at=bar.observed_at, available_at=bar.available_at,
                    source_sha256=node.source_sha256,
                ))
        previous = bar

    online = FourNodeStateMachine(max_age_bars=max_age_bars)
    streaming_results = tuple(online.apply(event) for event in events)
    _, historical_results = FourNodeStateMachine.replay(list(events), max_age_bars=max_age_bars)
    return ReplayComparison(tuple(events), streaming_results, tuple(historical_results), eligible_bars)


__all__ = ["ReplayBar", "ReplayComparison", "ReplayEligibility", "compare_stream_and_replay"]
