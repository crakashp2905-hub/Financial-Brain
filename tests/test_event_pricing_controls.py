from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.evaluation.event_pricing_controls import (
    EventSessionTape, IntradayClose, reaction_controls,
)


IST = timezone(timedelta(hours=5, minutes=30))
OPEN = datetime(2026, 11, 5, 9, 15, tzinfo=IST)


def tape(**changes):
    values = dict(
        previous_close_at=OPEN - timedelta(hours=17, minutes=45), previous_close=100.0,
        session_open_at=OPEN, session_open=102.0,
        session_close_at=OPEN + timedelta(hours=6, minutes=15), session_close=104.0,
        turnover=300.0, prior_turnovers=(100.0, 200.0, 150.0),
        intraday_closes=(IntradayClose(OPEN + timedelta(minutes=1), 102.1),
                         IntradayClose(OPEN + timedelta(minutes=2), 102.3)),
        same_weekday_close_returns_bps=(100.0, 200.0))
    values.update(changes)
    return EventSessionTape(**values)


def test_overnight_release_reports_gap_delay_turnover_and_weekday_excess():
    event_time = OPEN - timedelta(hours=2)
    result = reaction_controls(event_time, tape())
    assert result.release_phase == "OVERNIGHT" and result.overnight_release
    assert result.opening_gap_bps == pytest.approx(200)
    assert result.post_publication_delay_minutes == pytest.approx(120)
    assert result.post_publication_move_bps == pytest.approx(200)
    assert result.turnover_shock_ratio == pytest.approx(2)
    assert result.same_weekday_benchmark_bps == pytest.approx(150)
    assert result.weekday_excess_bps == pytest.approx(250)


def test_intraday_release_suppresses_pre_event_opening_gap():
    publication = OPEN + timedelta(minutes=1, seconds=30)
    result = reaction_controls(publication, tape())
    assert result.release_phase == "INTRADAY" and not result.overnight_release
    assert result.opening_gap_bps is None
    assert result.post_publication_delay_minutes == pytest.approx(0.5)
    assert result.post_publication_move_bps == pytest.approx((102.3 / 102.1 - 1) * 10_000)


def test_event_from_prior_session_does_not_misattribute_reaction_gap():
    result = reaction_controls(OPEN - timedelta(days=1), tape())
    assert result.release_phase == "PREVIOUS_SESSION"
    assert result.opening_gap_bps is None
    assert result.post_publication_move_bps is None


def test_missing_turnover_and_weekday_controls_are_reported_as_missing():
    result = reaction_controls(OPEN - timedelta(hours=1), tape(
        prior_turnovers=(), same_weekday_close_returns_bps=()))
    assert result.turnover_shock_ratio is None
    assert result.same_weekday_benchmark_bps is None
    assert result.weekday_excess_bps is None


def test_rejects_naive_times_and_unordered_intraday_data():
    with pytest.raises(ValueError, match="timezone-aware"):
        reaction_controls(datetime(2026, 11, 5), tape())
    unordered = (IntradayClose(OPEN + timedelta(minutes=2), 102.3),
                 IntradayClose(OPEN + timedelta(minutes=1), 102.1))
    with pytest.raises(ValueError, match="strictly ordered"):
        reaction_controls(OPEN + timedelta(minutes=1), tape(intraday_closes=unordered))
