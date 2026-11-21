from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.session_close import (
    CloseMark,
    OpenPosition,
    SessionCloseError,
    SessionCloseJournal,
    build_session_close_report,
)


def test_session_close_values_positions_and_checks_carry_and_expiry() -> None:
    cutoff = datetime(2026, 11, 15, 10, tzinfo=timezone.utc)
    positions = [
        OpenPosition("cash-1", "ABC", 10, "CASH", carry_authorized=True),
        OpenPosition("fut-1", "FUT", -2, "FUTURE"),
        OpenPosition("opt-1", "OPT", 1, "OPTION", expiry=date(2026, 11, 15), carry_authorized=True),
    ]
    marks = [
        CloseMark("ABC", "125.50", "fixture-close", "e1", cutoff - timedelta(seconds=5), cutoff),
        CloseMark("FUT", "99", "fixture-close", "e2", cutoff - timedelta(seconds=5), cutoff),
        CloseMark("OPT", "4.25", "fixture-settlement", "e3", cutoff - timedelta(seconds=5), cutoff),
    ]
    report = build_session_close_report(session_date=date(2026, 11, 15), next_session=date(2026, 11, 16),
                                        cutoff=cutoff, positions=positions, marks=marks)
    by_id = {row["position_id"]: row for row in report.rows}
    assert report.complete
    assert by_id["cash-1"]["disposition"] == "CARRY_ALLOWED"
    assert by_id["fut-1"]["disposition"] == "CARRY_BLOCKED"
    assert by_id["opt-1"]["disposition"] == "EXPIRY_MARKED"
    assert not by_id["opt-1"]["carry_to_next_session"]


def test_stale_missing_and_late_marks_never_authorize_carry() -> None:
    cutoff = datetime(2026, 11, 15, 10, tzinfo=timezone.utc)
    positions = [OpenPosition("stale", "A", 1, "CASH", carry_authorized=True),
                 OpenPosition("missing", "B", 1, "CASH", carry_authorized=True),
                 OpenPosition("late", "C", 1, "CASH", carry_authorized=True)]
    marks = [CloseMark("A", "10", "fixture", "a", cutoff - timedelta(hours=1), cutoff),
             CloseMark("C", "20", "fixture", "c", cutoff, cutoff + timedelta(seconds=1))]
    report = build_session_close_report(session_date=date(2026, 11, 15), next_session=date(2026, 11, 16),
                                        cutoff=cutoff, positions=positions, marks=marks)
    assert not report.complete
    assert {row["disposition"] for row in report.rows} == {"VALUATION_MISSING"}
    assert {row["reason"] for row in report.rows} == {
        "STALE_CLOSE_MARK", "MISSING_CLOSE_MARK", "MARK_NOT_AVAILABLE_BY_CUTOFF"}


def test_session_close_journal_is_immutable_and_idempotent(tmp_path) -> None:
    cutoff = datetime(2026, 11, 15, 10, tzinfo=timezone.utc)
    report = build_session_close_report(session_date=date(2026, 11, 15), next_session=date(2026, 11, 16),
                                        cutoff=cutoff, positions=[], marks=[])
    journal = SessionCloseJournal(tmp_path / "close.sqlite3")
    assert journal.append("close-1", report)
    assert not journal.append("close-1", report)
    assert journal.get("close-1")["report_hash"] == report.report_hash
    other = build_session_close_report(session_date=date(2026, 11, 15), next_session=date(2026, 11, 16),
                                       cutoff=cutoff, positions=[OpenPosition("p", "X", 1, "CASH")], marks=[])
    with pytest.raises(SessionCloseError, match="reused"):
        journal.append("close-1", other)
    journal.close()
