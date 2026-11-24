from datetime import date, datetime, timezone

import pytest

from financial_brain.forward_edge.cohort_archive import (
    CohortArchive,
    CohortArchiveError,
    CohortResult,
)


def _result(cohort, status, opportunities, reason="observed result"):
    return CohortResult(cohort, "variant-A", "strategy-sha", date(2026, 1, 1),
                        date(2026, 2, 1), status, 20, opportunities,
                        f"evidence-{cohort}", datetime(2026, 2, 2, tzinfo=timezone.utc), reason)


def test_negative_inconclusive_and_no_signal_cohorts_are_archived_and_discoverable(tmp_path) -> None:
    archive = CohortArchive(tmp_path / "cohorts.sqlite3")
    for result in (_result("negative", "NEGATIVE", 30),
                   _result("inconclusive", "INCONCLUSIVE", 15),
                   _result("no-signal", "NO_SIGNAL", 0, "scanner emitted no candidates")):
        assert archive.archive(result)
        assert not archive.archive(result)
    all_rows = archive.find(variant_id="variant-A", strategy_version_hash="strategy-sha")
    assert {row["result_class"] for row in all_rows} == {"NEGATIVE", "INCONCLUSIVE", "NO_SIGNAL"}
    assert len(all_rows) == 3
    archive.close()


def test_archive_is_immutable_and_no_signal_requires_zero_opportunities(tmp_path) -> None:
    archive = CohortArchive(tmp_path / "cohorts.sqlite3")
    result = _result("cohort-1", "NEGATIVE", 4)
    archive.archive(result)
    with pytest.raises(CohortArchiveError, match="immutable"):
        archive.archive(_result("cohort-1", "POSITIVE", 4))
    with pytest.raises(CohortArchiveError, match="zero opportunities"):
        _result("bad", "NO_SIGNAL", 1)
    archive.close()
