from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.paper.decision_journal import record, report


def test_journal_is_append_only_and_reports_decision_latency(tmp_path):
    path = tmp_path / "journal.jsonl"
    shown = datetime(2026, 10, 22, 10, tzinfo=timezone.utc)
    record(path, symbol="abc", choice="NO_TRADE", asof="2026-10-21",
           reason="No validated edge", evidence="Historical observation",
           shown_at=shown, decided_at=shown + timedelta(seconds=30))
    record(path, symbol="xyz", choice="WATCH", asof="2026-10-21",
           reason="Research only", evidence="Historical observation",
           shown_at=shown, decided_at=shown + timedelta(seconds=90))
    result = report(path)
    assert result["decisions"] == 2
    assert result["by_choice"] == {"NO_TRADE": 1, "WATCH": 1}
    assert result["median_decision_seconds"] == 60


def test_journal_rejects_backdated_decision(tmp_path):
    now = datetime(2026, 10, 22, 10, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="follow shown time"):
        record(tmp_path / "j.jsonl", symbol="ABC", choice="PAPER_TRADE", asof="2026-10-21",
               reason="A", evidence="B", shown_at=now, decided_at=now - timedelta(seconds=1))
