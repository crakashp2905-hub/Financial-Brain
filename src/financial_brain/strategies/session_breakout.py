"""NSE session breakouts: opening range, OCO intents, timeout and end-of-day flat, range exclusion, retest, failed
breakout, time-of-day placebo, circuit or halt abstention, and IST clock handling (ALG-054..063).

All clocks are Indian Standard Time. No US, London or Asian clock appears. Minute bars are dictionaries with keys
``time`` (HH:MM in IST), ``open``, ``high``, ``low``, ``close`` and ``volume``. A bar missing from the list is a
gap, not a fill.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from financial_brain.strategies.specs import decision_inputs_ok

IST = timezone(timedelta(hours=5, minutes=30))
NSE_OPEN = "09:15"  # ALG-054: exchange-calendar driven, not a fixed foreign clock
NSE_FLAT = "15:15"  # end-of-session flat rule (ALG-057)
NSE_CLOSE = "15:30"
RANGE_MINUTES = (5, 15, 30)  # ALG-055


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


@dataclass(frozen=True)
class OpeningRange:
    high: float
    low: float
    frozen_at: str


def opening_range(bars: list[dict[str, Any]], *, minutes: int = 15, start: str = NSE_OPEN) -> OpeningRange | None:
    """Range high and low, frozen only once the range window has fully closed (ALG-055).

    Returns ``None`` if the window has not finished or contains invalid bars.
    """
    if minutes not in RANGE_MINUTES:
        raise ValueError("range must be 5, 15 or 30 minutes")
    s = _minutes(start)
    end = s + minutes
    window = [b for b in bars if s <= _minutes(str(b["time"])) < end]
    last_needed = end - 1
    if not any(_minutes(str(b["time"])) == last_needed for b in window):
        return None  # the final minute of the range has not closed yet
    values: list[float | None] = []
    for b in window:
        values.extend([b["high"], b["low"]])
    if decision_inputs_ok(values) != "OK":
        return None
    high = max(float(b["high"]) for b in window)
    low = min(float(b["low"]) for b in window)
    return OpeningRange(high, low, f"{end // 60:02d}:{end % 60:02d}")


@dataclass(frozen=True)
class OcoIntent:
    buy_stop: float
    sell_stop: float


def oco_intents(rng: OpeningRange) -> OcoIntent:
    """Hypothetical one-cancels-other breakout intents on the range edges (ALG-056)."""
    if rng.high <= rng.low:
        raise ValueError("degenerate range")
    return OcoIntent(buy_stop=rng.high, sell_stop=rng.low)


@dataclass(frozen=True)
class SessionEvent:
    time: str
    kind: str
    price: float | None


def run_oco(bars: list[dict[str, Any]], rng: OpeningRange, *, flat_at: str = NSE_FLAT) -> list[SessionEvent]:
    """Simulate one breakout trade per session with the opposite stop cancelled on entry and a flat at ``flat_at``.

    Path ambiguity within one bar is resolved conservatively: a stop-out is assumed before a target. No target is
    used here, so the only exits are the opposite-side stop and the end-of-session flat.
    """
    events: list[SessionEvent] = []
    oco = oco_intents(rng)
    position: str | None = None
    entry_price: float | None = None
    for b in sorted(bars, key=lambda x: _minutes(str(x["time"]))):
        t = str(b["time"])
        if _minutes(t) < _minutes(rng.frozen_at):
            continue  # nothing triggers while the range is still forming
        if position is None:
            if float(b["high"]) >= oco.buy_stop:
                position, entry_price = "LONG", oco.buy_stop
                events.append(SessionEvent(t, "ENTRY_LONG", entry_price))
                events.append(SessionEvent(t, "CANCEL_SHORT_STOP", oco.sell_stop))
            elif float(b["low"]) <= oco.sell_stop:
                position, entry_price = "SHORT", oco.sell_stop
                events.append(SessionEvent(t, "ENTRY_SHORT", entry_price))
                events.append(SessionEvent(t, "CANCEL_LONG_STOP", oco.buy_stop))
        elif position == "LONG" and float(b["low"]) <= rng.low:
            events.append(SessionEvent(t, "STOP_LONG", rng.low))
            position = None
        elif position == "SHORT" and float(b["high"]) >= rng.high:
            events.append(SessionEvent(t, "STOP_SHORT", rng.high))
            position = None
        if position is not None and _minutes(t) >= _minutes(flat_at):
            events.append(SessionEvent(t, "FLAT_END_OF_SESSION", float(b["close"])))
            position = None
    if position is not None:
        events.append(SessionEvent("CLOSE", "FLAT_UNCLOSED_GUARD", None))  # should not happen; reported, not hidden
    return events


def range_excluded(rng: OpeningRange, prev_close: float, *, max_width_pct: float = 0.04, max_gap_pct: float = 0.02,
                   open_price: float | None = None) -> str | None:
    """Return a reason to abstain for a too-wide range or an opening gap (ALG-058), otherwise ``None``."""
    if decision_inputs_ok([prev_close]) != "OK":
        return "MISSING_PRIOR_CLOSE"
    width = (rng.high - rng.low) / prev_close
    if width > max_width_pct:
        return "RANGE_TOO_WIDE"
    if open_price is not None and abs(open_price / prev_close - 1) > max_gap_pct:
        return "OPENING_GAP"
    return None


def retest_signal(closes: list[float], highs: list[float], lows: list[float], level: float) -> int | None:
    """Breakout-and-retest (ALG-059): a close above ``level``, then a later touch of ``level`` that closes back above.

    The retest must occur strictly after the confirming close.
    """
    confirmed: int | None = None
    for i, c in enumerate(closes):
        if confirmed is None:
            if c > level:
                confirmed = i
            continue
        if i > confirmed and lows[i] <= level and c > level:
            return i
    return None


def failed_breakout_signal(closes: list[float], lows: list[float], level: float, *, window: int = 5) -> int | None:
    """Spring-style false break (ALG-060): penetration below ``level`` then a close back above it within ``window``."""
    for i in range(len(closes)):
        if lows[i] < level and closes[i] > level:
            return i
        if lows[i] < level:
            for j in range(i + 1, min(len(closes), i + window + 1)):
                if closes[j] > level:
                    return j
            return None
    return None


def placebo_signal_times(bars: list[dict[str, Any]], clock: str = "09:30") -> list[str]:
    """Time-of-day placebo (ALG-061): the same IST clock as a comparison, with no geometry."""
    target = _minutes(clock)
    return [str(b["time"]) for b in bars if _minutes(str(b["time"])) == target]


def fill_if_available(bar: dict[str, Any] | None, *, halted: bool = False) -> float | None:
    """Circuits, halts and auction unavailability produce no fill and no invented price (ALG-062)."""
    if bar is None or halted:
        return None
    if float(bar.get("volume", 0.0)) <= 0:
        return None
    price = float(bar["close"])
    return price if decision_inputs_ok([price]) == "OK" else None


def ist_clock(utc_iso: str) -> str:
    """Convert a timezone-aware UTC timestamp to the IST HH:MM used by session rules (ALG-063)."""
    ts = datetime.fromisoformat(utc_iso)
    if ts.tzinfo is None:
        raise ValueError("naive timestamp refused")
    return ts.astimezone(IST).strftime("%H:%M")
