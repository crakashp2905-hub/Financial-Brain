from __future__ import annotations

import math

import pytest

from financial_brain.forward_edge.family_overlap import FamilyOverlapError, compare_rank_families


def test_identical_low_risk_ranks_are_flagged_as_overlapping() -> None:
    left = {f"S{i}": float(i) for i in range(20)}
    right = {name: value * 1.5 for name, value in left.items()}
    result = compare_rank_families("low_vol", left, "low_atr", right)
    assert result.matched_names == 20
    assert result.spearman_rank_correlation == pytest.approx(1.0)
    assert result.jaccard_overlap == pytest.approx(1.0)
    assert result.classification == "OVERLAPPING"


def test_overlap_uses_only_matched_finite_values_without_imputation() -> None:
    left = {f"S{i}": float(i) for i in range(10)}
    right = {f"S{i}": float(9 - i) for i in range(9)}
    right["S8"] = math.nan
    result = compare_rank_families("low_vol", left, "low_atr", right, min_names=3)
    assert result.matched_names == 8
    assert result.spearman_rank_correlation == pytest.approx(-1.0)
    assert result.classification == "DISTINCT_OR_UNPROVEN"


def test_overlap_rejects_weak_or_invalid_inputs() -> None:
    with pytest.raises(FamilyOverlapError, match="matched finite"):
        compare_rank_families("low_vol", {"A": 1.0}, "low_atr", {"B": 2.0}, min_names=3)
    with pytest.raises(FamilyOverlapError, match="quantile"):
        compare_rank_families("low_vol", {str(i): float(i) for i in range(4)}, "low_atr",
                              {str(i): float(i) for i in range(4)}, quantile=0.7)
