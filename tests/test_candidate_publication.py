from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.candidate_publication import (
    CandidatePublicationError, RankedCandidate, publish_candidate_batch,
)
from financial_brain.kernel.trader_signal import DeskId, EvidenceTier, SignalSide, TraderSignal


def _row(index: int, desk: DeskId, score: int, *, eligible: bool = True, second: int = 0):
    now = datetime(2026, 11, 21, 5, 0, tzinfo=timezone.utc) + timedelta(seconds=second)
    signal = TraderSignal(
        signal_id=f"550e8400-e29b-41d4-a716-4466554400{index:02d}", desk_id=desk,
        strategy_id="test", strategy_version="1", strategy_hash="a" * 64,
        instrument_id=f"NSE:TEST{index}", side=SignalSide.BUY, as_of=now,
        decision_at=now, expires_at=now + timedelta(minutes=1),
        evidence_tier=EvidenceTier.LOCAL_PAPER, source_hash="b" * 64,
    )
    return RankedCandidate(signal, Decimal(score), eligible)


def test_daily_swing_is_capped_five_and_etf_is_ranked_independently():
    rows = tuple(_row(i, DeskId.SWING, i) for i in range(1, 8)) + (
        _row(20, DeskId.ETF, 2), _row(21, DeskId.ETF, 100),
    )
    output = publish_candidate_batch(rows, session_date=date(2026, 11, 21))
    assert len(output.swing) == 5
    assert [int(row.ranking_value) for row in output.swing] == [7, 6, 5, 4, 3]
    assert [int(row.ranking_value) for row in output.etf] == [100, 2]


def test_intraday_and_options_are_event_driven_without_minimum_quota():
    output = publish_candidate_batch(
        (_row(30, DeskId.INTRADAY, 3, second=20), _row(31, DeskId.INTRADAY, 9, second=10),
         _row(32, DeskId.OPTIONS, 0)),
        session_date=date(2026, 11, 21),
    )
    assert [row.signal.instrument_id for row in output.intraday_events] == ["NSE:TEST31", "NSE:TEST30"]
    assert len(output.options_events) == 1
    empty = publish_candidate_batch((), session_date=date(2026, 11, 21))
    assert empty.intraday_events == empty.options_events == ()


def test_ineligible_and_nonpositive_swing_scores_are_not_published():
    output = publish_candidate_batch(
        (_row(40, DeskId.SWING, 10, eligible=False), _row(41, DeskId.SWING, 0)),
        session_date=date(2026, 11, 21),
    )
    assert output.swing == ()


def test_candidate_from_another_session_is_rejected():
    with pytest.raises(CandidatePublicationError, match="does not match"):
        publish_candidate_batch((_row(50, DeskId.SWING, 1),), session_date=date(2026, 11, 20))
