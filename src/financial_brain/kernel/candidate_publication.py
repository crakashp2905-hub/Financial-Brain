"""Desk-separated candidate publication policies for signal output."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timezone
from decimal import Decimal, InvalidOperation

from ..kernel.trading_clock import IST
from .trader_signal import DeskId, TraderSignal


class CandidatePublicationError(ValueError):
    """Candidate cards conflict with desk, session, or ranking policy."""


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    signal: TraderSignal
    ranking_value: Decimal
    eligible: bool

    def __post_init__(self) -> None:
        try:
            score = Decimal(self.ranking_value)
        except (InvalidOperation, TypeError) as exc:
            raise CandidatePublicationError("ranking_value must be decimal") from exc
        if not score.is_finite():
            raise CandidatePublicationError("ranking_value must be finite")
        object.__setattr__(self, "ranking_value", score)


@dataclass(frozen=True, slots=True)
class CandidatePublication:
    session_date: date
    swing: tuple[RankedCandidate, ...]
    etf: tuple[RankedCandidate, ...]
    intraday_events: tuple[RankedCandidate, ...]
    options_events: tuple[RankedCandidate, ...]


def publish_candidate_batch(
    candidates: tuple[RankedCandidate, ...],
    *,
    session_date: date,
    max_daily_swing: int = 5,
) -> CandidatePublication:
    """Rank swing/ETF independently and preserve event-driven desk output without quotas."""
    if max_daily_swing < 0:
        raise CandidatePublicationError("max_daily_swing cannot be negative")
    groups: dict[DeskId, list[RankedCandidate]] = {desk: [] for desk in DeskId}
    for candidate in candidates:
        known_on = candidate.signal.as_of.astimezone(IST).date()
        if known_on != session_date:
            raise CandidatePublicationError("candidate as_of date does not match session_date")
        groups[candidate.signal.desk_id].append(candidate)

    def ranked(rows: list[RankedCandidate]) -> tuple[RankedCandidate, ...]:
        return tuple(sorted((row for row in rows if row.eligible),
                            key=lambda row: (-row.ranking_value, row.signal.signal_id)))

    def events(rows: list[RankedCandidate]) -> tuple[RankedCandidate, ...]:
        return tuple(sorted((row for row in rows if row.eligible),
                            key=lambda row: (row.signal.decision_at.astimezone(timezone.utc),
                                             row.signal.signal_id)))

    swing = tuple(row for row in ranked(groups[DeskId.SWING])
                  if row.ranking_value > 0)[:max_daily_swing]
    return CandidatePublication(
        session_date=session_date,
        swing=swing,
        etf=ranked(groups[DeskId.ETF]),
        intraday_events=events(groups[DeskId.INTRADAY]),
        options_events=events(groups[DeskId.OPTIONS]),
    )


__all__ = ["CandidatePublication", "CandidatePublicationError", "RankedCandidate",
           "publish_candidate_batch"]
