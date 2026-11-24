from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.outcome_maturation import (
    OutcomeCandidate,
    OutcomeMaturationError,
    build_outcome_maturation_queue,
)


NOW = datetime(2026, 11, 16, 4, tzinfo=timezone.utc)


def _candidate(outcome_id, *, exit_at=None, embargo=0, label=None, source=None):
    return OutcomeCandidate(outcome_id, "swing-v1", "v2", NOW - timedelta(days=5),
                            "a" * 64, exit_at,
                            NOW + timedelta(hours=embargo), label, source)


def test_unfinished_and_embargoed_outcomes_never_enter_training_examples():
    report = build_outcome_maturation_queue(candidates=(
        _candidate("open"),
        _candidate("future-exit", exit_at=NOW + timedelta(hours=1), label=0.25, source="b" * 64),
        _candidate("embargo", exit_at=NOW - timedelta(hours=1), embargo=2,
                   label=-0.20, source="c" * 64),
        _candidate("mature", exit_at=NOW - timedelta(hours=3), label=0.10, source="d" * 64),
    ), as_of=NOW)
    assert [row.outcome_id for row in report.matured] == ["mature"]
    assert {row.outcome_id: row.status for row in report.pending} == {
        "open": "EXIT_INCOMPLETE", "future-exit": "EXIT_NOT_YET_COMPLETED",
        "embargo": "EMBARGO_ACTIVE"}
    assert all(not hasattr(row, "realized_net_return") for row in report.pending)
    assert len(report.report_hash) == 64


def test_missing_outcome_label_hash_is_not_released():
    report = build_outcome_maturation_queue(candidates=(
        _candidate("missing", exit_at=NOW - timedelta(hours=1)),
    ), as_of=NOW)
    assert not report.matured
    assert report.pending[0].status == "LABEL_SOURCE_MISSING"


def test_unfinished_candidate_cannot_be_created_with_early_realized_label():
    with pytest.raises(OutcomeMaturationError, match="unfinished outcomes"):
        _candidate("leak", label=0.5, source="e" * 64)


def test_duplicate_ids_and_naive_asof_are_rejected():
    item = _candidate("same")
    with pytest.raises(OutcomeMaturationError, match="unique"):
        build_outcome_maturation_queue(candidates=(item, item), as_of=NOW)
    with pytest.raises(OutcomeMaturationError, match="timezone aware"):
        build_outcome_maturation_queue(candidates=(item,), as_of=datetime(2026, 11, 16))
