from datetime import date, datetime, timezone

import pytest

from financial_brain.forward_edge.completeness import (
    CompletenessError,
    CompletenessJournal,
    build_completeness_report,
)


def test_missing_segments_or_close_marks_downgrade_evidence() -> None:
    report = build_completeness_report(
        session_id="s1", session_date=date(2026, 11, 16),
        generated_at=datetime(2026, 11, 16, 11, tzinfo=timezone.utc),
        expected_segments=("preflight", "decisions", "orders", "fills", "close"),
        received_segments=("preflight", "decisions", "orders", "fills"),
        close_report_hash="abc", close_report_complete=False,
    )
    assert report.evidence_status == "EVIDENCE_DOWNGRADED"
    assert report.missing_segments == ("close",)
    assert not report.eligible


def test_only_complete_incident_free_session_is_eligible() -> None:
    args = dict(session_id="s2", session_date=date(2026, 11, 16),
                generated_at=datetime(2026, 11, 16, 11, tzinfo=timezone.utc),
                expected_segments=("preflight", "orders", "close"),
                received_segments=("preflight", "orders", "close"),
                close_report_hash="close-hash", close_report_complete=True)
    good = build_completeness_report(**args)
    assert good.eligible
    assert good.evidence_status == "ELIGIBLE"
    incident = build_completeness_report(**{**args, "incidents": ("ledger-difference",)})
    assert not incident.eligible
    assert incident.evidence_status == "EVIDENCE_DOWNGRADED"


def test_completeness_journal_is_immutable_idempotent_and_restartable(tmp_path) -> None:
    report = build_completeness_report(
        session_id="s3", session_date=date(2026, 11, 16),
        generated_at=datetime(2026, 11, 16, 11, tzinfo=timezone.utc),
        expected_segments=("close",), received_segments=("close",),
        close_report_hash="close-hash", close_report_complete=True)
    path = tmp_path / "completeness.sqlite3"
    journal = CompletenessJournal(path)
    assert journal.append(report)
    assert not journal.append(report)
    journal.close()
    restored = CompletenessJournal(path)
    assert restored.get("s3")["report_hash"] == report.report_hash
    changed = build_completeness_report(
        session_id="s3", session_date=date(2026, 11, 16),
        generated_at=datetime(2026, 11, 16, 12, tzinfo=timezone.utc),
        expected_segments=("close",), received_segments=("close",),
        close_report_hash="close-hash", close_report_complete=True)
    with pytest.raises(CompletenessError, match="immutable"):
        restored.append(changed)
    restored.close()
