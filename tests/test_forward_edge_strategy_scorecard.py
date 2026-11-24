from datetime import date, timedelta

import pytest

from financial_brain.forward_edge.bootstrap import DatedAlpha
from financial_brain.forward_edge.capacity import CapacityReport
from financial_brain.forward_edge.strategy_scorecard import (
    StrategyCohort,
    StrategyScorecardError,
    build_strategy_scorecard,
    render_strategy_scorecard,
)


def _observations(values, *, start=date(2026, 1, 1)):
    return tuple(DatedAlpha(start + timedelta(days=index), value)
                 for index, value in enumerate(values))


def test_scorecard_only_compares_same_horizon_and_session_cohorts(tmp_path):
    capacity = CapacityReport((), 25_000, "ATTESTED_TEST_FIXTURE", "a" * 64, "b" * 64)
    cohorts = (
        StrategyCohort("swing-a", "cohort-q1", 5, _observations((0.01, -0.005, 0.02)), capacity),
        StrategyCohort("swing-b", "cohort-q1", 5, _observations((0.0, 0.003, 0.006)), None),
        StrategyCohort("long-term", "cohort-q1", 252, _observations((0.02, 0.01, 0.03)), None),
    )
    report = build_strategy_scorecard(cohorts=cohorts, expected_block_length=2,
                                      resamples=300, confidence_level=0.9)
    assert len(report.groups) == 2
    matched = next(group for group in report.groups if group.horizon_sessions == 5)
    assert {row.strategy_id for row in matched.strategies} == {"swing-a", "swing-b"}
    assert matched.strategies[0].capacity_max_notional == 25_000
    assert report.promotion_inference_allowed is False
    page = render_strategy_scorecard(report, tmp_path / "scorecard.html").read_text(encoding="utf-8")
    assert "horizon 5 session(s)" in page
    assert "horizon 252 session(s)" in page
    assert "do not authorize promotion" in page


def test_unaligned_dates_are_separate_groups_not_directly_ranked():
    report = build_strategy_scorecard(cohorts=(
        StrategyCohort("a", "same-horizon", 1, _observations((0.1, 0.2)), None),
        StrategyCohort("b", "same-horizon", 1,
                       _observations((0.1, 0.2), start=date(2026, 1, 2)), None),
    ), expected_block_length=1, resamples=200)
    assert len(report.groups) == 2
    assert report.groups[0].sessions != report.groups[1].sessions


def test_duplicate_series_and_too_short_series_are_rejected():
    cohort = StrategyCohort("a", "c", 1, _observations((0.0, 0.1)), None)
    with pytest.raises(StrategyScorecardError, match="duplicate"):
        build_strategy_scorecard(cohorts=(cohort, cohort), expected_block_length=1, resamples=200)
    with pytest.raises(StrategyScorecardError, match="at least two"):
        StrategyCohort("short", "c", 1, _observations((0.1,)), None)
