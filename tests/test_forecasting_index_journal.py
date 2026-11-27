import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from financial_brain.forecasting.index_journal import (
    ForecastIssue,
    ForecastJournalError,
    append_issue,
    connect,
    expected_session_intervals,
    missing_intervals,
)

IST = ZoneInfo("Asia/Kolkata")
DAY = date(2026, 11, 25)
END = datetime(2026, 11, 25, 9, 30, tzinfo=IST)


def issue(**changes):
    fields = {"session_date": DAY, "interval_end": END,
              "decision_at": END + timedelta(seconds=5),
              "issued_at": END + timedelta(seconds=6),
              "model_sha256": "a" * 64, "feature_manifest_sha256": "b" * 64,
              "status": "ISSUED", "probabilities": {"UP": .6, "DOWN": .2, "FLAT": .2},
              "source_observed_at": END, "source_available_at": END + timedelta(seconds=5),
              "max_quote_age_ms": 1000, "abstention_or_miss_reason": None}
    return ForecastIssue(**(fields | changes))


def test_issue_ledger_is_idempotent_and_exposes_missing_intervals(tmp_path):
    con = connect(tmp_path / "journal.sqlite")
    try:
        first = append_issue(con, issue())
        second = append_issue(con, issue())
        assert first["idempotent_retry"] is False
        assert second["idempotent_retry"] is True
        assert len(first["payload_sha256"]) == 64
        expected = (END, END + timedelta(minutes=15))
        assert missing_intervals(con, DAY, expected) == [expected[1].isoformat()]
        assert len(expected_session_intervals(DAY)) == 25
    finally:
        con.close()


def test_journal_rejects_stale_issued_data_and_conflicting_replays(tmp_path):
    con = connect(tmp_path / "journal.sqlite")
    try:
        with pytest.raises(ForecastJournalError, match="stale quote"):
            append_issue(con, issue(source_available_at=END + timedelta(seconds=5),
                                    decision_at=END + timedelta(seconds=10),
                                    issued_at=END + timedelta(seconds=11), max_quote_age_ms=100))
        append_issue(con, issue())
        with pytest.raises(ForecastJournalError, match="conflicting replay"):
            append_issue(con, issue(status="ABSTAINED", abstention_or_miss_reason="uncertain"))
    finally:
        con.close()


def test_missed_and_abstained_intervals_are_explicit_and_append_only(tmp_path):
    con = connect(tmp_path / "journal.sqlite")
    try:
        miss = issue(status="MISSED", probabilities=None, source_observed_at=None,
                     source_available_at=None, abstention_or_miss_reason="feed outage")
        append_issue(con, miss)
        next_end = END + timedelta(minutes=15)
        abstain = issue(interval_end=next_end, decision_at=next_end + timedelta(seconds=5),
                        issued_at=next_end + timedelta(seconds=6),
                        source_observed_at=next_end, source_available_at=next_end + timedelta(seconds=5),
                        status="ABSTAINED", abstention_or_miss_reason="low confidence")
        append_issue(con, abstain)
        rows = con.execute("SELECT status, probabilities_json FROM forecast_issues ORDER BY status").fetchall()
        assert rows[0]["status"] == "ABSTAINED"
        assert rows[1]["status"] == "MISSED" and rows[1]["probabilities_json"] is None
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute("DELETE FROM forecast_issues")
    finally:
        con.close()
