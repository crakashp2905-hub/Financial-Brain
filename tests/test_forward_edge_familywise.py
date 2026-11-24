from datetime import date, timedelta
import random

import pytest

from financial_brain.forward_edge.familywise import (
    FamilyReturnRow,
    FamilywiseError,
    white_reality_check,
)


def _rows(*, edge=0.0, n=45):
    rng = random.Random(801)
    start = date(2026, 1, 1)
    rows = []
    for index in range(n):
        common = rng.gauss(0, 0.01)
        rows.append(FamilyReturnRow(start + timedelta(days=index), 0.0,
                                    {"candidate-A": edge + common + rng.gauss(0, 0.005),
                                     "candidate-B": common + rng.gauss(0, 0.005),
                                     "candidate-C": rng.gauss(0, 0.01)}))
    return tuple(rows)


def test_joint_stationary_reality_check_is_reproducible_and_family_wide() -> None:
    rows = _rows()
    report = white_reality_check(rows, expected_block_length=5, resamples=500, seed=22)
    assert report == white_reality_check(rows, expected_block_length=5, resamples=500, seed=22)
    assert report.candidate_count == 3
    assert report.candidate_ids == ("candidate-A", "candidate-B", "candidate-C")
    assert 0 < report.familywise_p_value <= 1
    assert "WHITE_REALITY_CHECK" in report.method


def test_familywise_adjustment_flags_large_persistent_alpha() -> None:
    report = white_reality_check(_rows(edge=0.03), expected_block_length=4,
                                 resamples=500, seed=3)
    assert report.observed_best_mean_alpha > 0.02
    assert report.familywise_p_value <= 0.01


def test_frozen_candidate_universe_must_be_complete_and_chronological() -> None:
    rows = _rows(n=10)
    incomplete = (rows[0], FamilyReturnRow(rows[1].session, 0.0, {"candidate-A": 0.0}), *rows[2:])
    with pytest.raises(FamilywiseError, match="every strategy"):
        white_reality_check(incomplete, expected_block_length=2, resamples=200)
    with pytest.raises(FamilywiseError, match="chronological"):
        white_reality_check((rows[1], rows[0]), expected_block_length=1, resamples=200)
