from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.session_timeline import (
    SessionTimelineError,
    SessionTimelineJournal,
    TimelineEvent,
    build_session_timeline,
    render_session_timeline,
)


NOW = datetime(2026, 11, 16, 4, tzinfo=timezone.utc)


def _event(session_id, event_id, kind, seconds=0, details="ok"):
    return TimelineEvent(session_id, event_id, kind, NOW + timedelta(seconds=seconds),
                         "paper-journal", "a" * 64, details)


def test_timeline_keeps_expected_zero_evidence_sessions_in_denominator(tmp_path):
    events = (
        _event("s1", "1-pre", "PREFLIGHT"), _event("s1", "1-sig", "SIGNAL", 1),
        _event("s1", "1-fill", "FILL", 2), _event("s1", "1-close", "SESSION_CLOSE", 3),
        _event("s2", "2-pre", "PREFLIGHT"), _event("s2", "2-no", "NO_TRADE", 1),
        _event("s2", "2-close", "SESSION_CLOSE", 3),
    )
    report = build_session_timeline(expected_session_ids=("s1", "s2", "s3"),
                                    events=events, as_of=NOW + timedelta(hours=1))
    assert len(report.rows) == 3
    assert report.rows[0].complete
    assert report.rows[1].complete
    assert report.rows[2].missing_categories == ("PREFLIGHT", "DECISION", "SESSION_CLOSE")
    assert not report.rows[2].complete
    page = render_session_timeline(report, tmp_path / "timeline.html").read_text(encoding="utf-8")
    assert "Expected sessions: 3" in page
    assert "No events recorded" in page


def test_timeline_refuses_future_and_unexpected_session_events():
    with pytest.raises(SessionTimelineError, match="future"):
        build_session_timeline(expected_session_ids=("s1",),
                               events=(_event("s1", "future", "PREFLIGHT", 60),), as_of=NOW)
    with pytest.raises(SessionTimelineError, match="outside the expected denominator"):
        build_session_timeline(expected_session_ids=("s1",),
                               events=(_event("s2", "other", "PREFLIGHT"),), as_of=NOW)


def test_timeline_journal_is_idempotent_durable_and_immutable(tmp_path):
    path = tmp_path / "timeline.sqlite3"
    journal = SessionTimelineJournal(path)
    event = _event("s1", "ev1", "NO_TRADE", details="spread too wide")
    assert journal.append(event)
    assert not journal.append(event)
    expected = journal.events("s1")
    journal.close()
    reopened = SessionTimelineJournal(path)
    assert reopened.events("s1") == expected
    changed = _event("s1", "ev1", "NO_TRADE", details="changed")
    with pytest.raises(SessionTimelineError, match="changed content"):
        reopened.append(changed)
    reopened.close()
