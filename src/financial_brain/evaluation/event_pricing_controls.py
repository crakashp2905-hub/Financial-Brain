"""Deterministic controls for separating event reaction from already-priced movement."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from statistics import fmean, median
from typing import Literal


@dataclass(frozen=True, slots=True)
class IntradayClose:
    closed_at: datetime
    close: float


@dataclass(frozen=True, slots=True)
class EventSessionTape:
    previous_close_at: datetime
    previous_close: float
    session_open_at: datetime
    session_open: float
    session_close_at: datetime
    session_close: float
    turnover: float
    prior_turnovers: tuple[float, ...]
    intraday_closes: tuple[IntradayClose, ...]
    same_weekday_close_returns_bps: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class EventPricingControls:
    release_phase: Literal["PREVIOUS_SESSION", "OVERNIGHT", "INTRADAY", "AFTER_REACTION_SESSION"]
    opening_gap_bps: float | None
    overnight_release: bool
    post_publication_delay_minutes: float | None
    post_publication_move_bps: float | None
    turnover_shock_ratio: float | None
    session_close_return_bps: float
    same_weekday_benchmark_bps: float | None
    weekday_excess_bps: float | None


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _bps(new: float, old: float) -> float:
    return (new / old - 1.0) * 10_000


def reaction_controls(published_at: datetime, tape: EventSessionTape) -> EventPricingControls:
    """Compute reaction controls from point-in-time event and market observations.

    For an intraday release, the first close at/after publication is compared with the
    last close before it (or session open when no such close exists). Opening gap is
    suppressed because it predates an intraday release. Turnover baseline contains only
    supplied prior sessions; weekday controls must be precomputed matched observations.
    """
    timestamps = (published_at, tape.previous_close_at, tape.session_open_at, tape.session_close_at)
    for index, value in enumerate(timestamps):
        _aware(value, ("published_at", "previous_close_at", "session_open_at", "session_close_at")[index])
    prices = (tape.previous_close, tape.session_open, tape.session_close, tape.turnover)
    if not all(isfinite(value) and value > 0 for value in prices):
        raise ValueError("prices and turnover must be finite and positive")
    if not tape.previous_close_at < tape.session_open_at < tape.session_close_at:
        raise ValueError("session timestamps are not ordered")
    for row in tape.intraday_closes:
        _aware(row.closed_at, "intraday close time")
        if not isfinite(row.close) or row.close <= 0:
            raise ValueError("intraday close must be finite and positive")
    if any(a.closed_at >= b.closed_at for a, b in zip(tape.intraday_closes,
                                                       tape.intraday_closes[1:])):
        raise ValueError("intraday closes must be strictly ordered")
    if any(not tape.session_open_at <= row.closed_at <= tape.session_close_at
           for row in tape.intraday_closes):
        raise ValueError("intraday close is outside the reaction session")
    if any(not isfinite(value) or value <= 0 for value in tape.prior_turnovers):
        raise ValueError("prior turnovers must be finite and positive")
    if any(not isfinite(value) for value in tape.same_weekday_close_returns_bps):
        raise ValueError("weekday benchmark returns must be finite")

    if published_at < tape.previous_close_at:
        phase: Literal["PREVIOUS_SESSION", "OVERNIGHT", "INTRADAY", "AFTER_REACTION_SESSION"] = "PREVIOUS_SESSION"
    elif published_at <= tape.session_open_at:
        phase = "OVERNIGHT"
    elif published_at <= tape.session_close_at:
        phase = "INTRADAY"
    else:
        phase = "AFTER_REACTION_SESSION"

    gap = _bps(tape.session_open, tape.previous_close) if phase == "OVERNIGHT" else None
    delay: float | None = None
    post_move: float | None = None
    if phase == "INTRADAY":
        before = [row for row in tape.intraday_closes if row.closed_at < published_at]
        after = next((row for row in tape.intraday_closes if row.closed_at >= published_at), None)
        if after is not None:
            before_price = before[-1].close if before else tape.session_open
            delay = (after.closed_at - published_at).total_seconds() / 60
            post_move = _bps(after.close, before_price)
    elif gap is not None:
        delay = (tape.session_open_at - published_at).total_seconds() / 60
        post_move = gap

    turnover_ratio = (tape.turnover / median(tape.prior_turnovers)
                      if tape.prior_turnovers else None)
    session_return = _bps(tape.session_close, tape.previous_close)
    weekday = (fmean(tape.same_weekday_close_returns_bps)
               if tape.same_weekday_close_returns_bps else None)
    return EventPricingControls(
        release_phase=phase, opening_gap_bps=gap,
        overnight_release=phase == "OVERNIGHT",
        post_publication_delay_minutes=delay,
        post_publication_move_bps=post_move,
        turnover_shock_ratio=turnover_ratio,
        session_close_return_bps=session_return,
        same_weekday_benchmark_bps=weekday,
        weekday_excess_bps=None if weekday is None else session_return - weekday)
