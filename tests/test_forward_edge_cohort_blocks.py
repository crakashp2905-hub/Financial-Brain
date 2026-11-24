from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.cohort_blocks import (
    BlockOutcome,
    ChronologicalBlockPlan,
    CohortBlockError,
    evaluate_chronological_blocks,
)


def _plan():
    start = date(2026, 1, 1)
    return ChronologicalBlockPlan("blocks-v1", datetime(2025, 12, 30, tzinfo=timezone.utc),
                                  start, (start, start + timedelta(days=10), start + timedelta(days=20)))


def _outcomes(values):
    start = date(2026, 1, 1)
    return tuple(BlockOutcome(start + timedelta(days=i), value,
                              "HIGH_VOL" if i % 2 else "LOW_VOL") for i, value in enumerate(values))


def test_frozen_chronological_blocks_report_regime_mix_and_return_by_period() -> None:
    values = [0.01] * 10 + [0.01] * 10 + [-0.005] * 10
    report = evaluate_chronological_blocks(_plan(), _outcomes(values))
    assert [block["sessions"] for block in report.blocks] == [10, 10, 10]
    assert report.blocks[0]["regime_counts"] == {"HIGH_VOL": 5, "LOW_VOL": 5}
    assert report.positive_blocks == 2
    assert report.largest_positive_block_share == pytest.approx(0.5)
    assert not report.single_lucky_period_flag
    assert not report.promotion_allowed


def test_single_period_cannot_hide_as_overall_positive_mean() -> None:
    values = [0.02] * 10 + [-0.005] * 20
    report = evaluate_chronological_blocks(_plan(), _outcomes(values))
    assert report.overall_mean_net_alpha > 0
    assert report.positive_blocks == 1
    assert report.single_lucky_period_flag


def test_block_plan_must_be_preregistered_and_cover_outcomes() -> None:
    start = date(2026, 1, 1)
    with pytest.raises(CohortBlockError, match="three"):
        ChronologicalBlockPlan("bad", datetime(2025, 12, 30, tzinfo=timezone.utc), start,
                               (start, start + timedelta(days=10)))
    with pytest.raises(CohortBlockError, match="predates"):
        evaluate_chronological_blocks(_plan(), (BlockOutcome(start - timedelta(days=1), 0.0, "UNKNOWN"),))
