"""FE-0049 durable journal keeps no-signal and unsuccessful sessions in the denominator."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from financial_brain.forward_edge.decision_journal import DecisionJournal, DecisionJournalError, DecisionRecord

NOW = datetime(2026, 11, 15, 4, 0, tzinfo=timezone.utc)


def _record(strategy: str, outcome: str, reason: str = "") -> DecisionRecord:
    return DecisionRecord("session-1", strategy, f"decision-{strategy}", outcome, NOW,
                          (reason,) if reason else (), "a" * 64, {"signal_count": 0})


def test_journal_persists_all_outcomes_and_keeps_zero_signal_denominator(tmp_path) -> None:
    journal = DecisionJournal(tmp_path / "decisions.sqlite3")
    try:
        for row in (_record("momentum", "NO_SIGNAL", "NO_QUALIFYING_SETUP"),
                    _record("orb", "ABSTAINED", "STALE_QUOTE"),
                    _record("options", "UNSUCCESSFUL_SESSION", "DATA_FEED_DOWN")):
            assert journal.append(row)
        assert not journal.append(_record("momentum", "NO_SIGNAL", "NO_QUALIFYING_SETUP"))
        summary = journal.session_summary("session-1", {"momentum", "orb", "options"})
        assert summary["complete"] is True
        assert summary["eligible_strategy_count"] == 3
        assert summary["outcome_counts"]["NO_SIGNAL"] == 1
        assert summary["outcome_counts"]["ABSTAINED"] == 1
        assert summary["outcome_counts"]["UNSUCCESSFUL_SESSION"] == 1
    finally:
        journal.close()


def test_journal_rejects_conflicts_missing_reasons_and_unregistered_strategies(tmp_path) -> None:
    journal = DecisionJournal(tmp_path / "decisions.sqlite3")
    try:
        original = _record("momentum", "SUBMITTED")
        journal.append(original)
        changed = DecisionRecord("session-1", "momentum", original.decision_id, "WITHHELD", NOW,
                                 ("RISK",), "a" * 64, {})
        with pytest.raises(DecisionJournalError, match="different content"):
            journal.append(changed)
        with pytest.raises(DecisionJournalError, match="explicit reason"):
            DecisionRecord("session-1", "orb", "bad", "ABSTAINED", NOW, (), "a" * 64, {})
        with pytest.raises(DecisionJournalError, match="outside"):
            journal.session_summary("session-1", {"orb"})
    finally:
        journal.close()


def test_journal_recovers_decisions_after_restart_and_reports_missing_cohort_members(tmp_path) -> None:
    path = tmp_path / "decisions.sqlite3"
    first = DecisionJournal(path)
    first.append(_record("momentum", "NO_SIGNAL", "NO_SETUP"))
    first.close()
    second = DecisionJournal(path)
    try:
        summary = second.session_summary("session-1", {"momentum", "orb"})
        assert summary["complete"] is False
        assert summary["missing_strategy_ids"] == ["orb"]
    finally:
        second.close()
