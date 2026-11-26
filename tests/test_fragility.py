from datetime import date, timedelta

import pytest

from financial_brain.research.fragility import FragilityError, NetOutcomeSlice, build_fragility_report


def _row(index: int, net: float = 0.01) -> NetOutcomeSlice:
    return NetOutcomeSlice(f"o{index}", date(2026, 1, 1) + timedelta(days=index % 4),
                           date(2025, 12, 20) + timedelta(days=index % 3),
                           f"S{index % 6}", "LARGE" if index % 2 else "SMALL",
                           "BULL" if index % 3 else "BEAR", "LOW", "1-5D", "2X_COST" if index % 4 else "BASE",
                           "HIGH" if index % 5 == 0 else "LOW", "FILTER_A" if index % 2 else "NONE",
                           net, "a" * 64)


def test_fragility_report_covers_required_dimensions_and_marks_sparse_groups() -> None:
    report = build_fragility_report(tuple(_row(i) for i in range(30)), min_group_size=5)
    assert set(report.dimension_summaries) == {
        "source_date", "session", "instrument_bucket", "regime", "exposure_bucket", "holding_bucket",
        "cost_level", "slippage_bucket", "exclusion_filter",
    }
    assert set(report.dimension_summaries["source_date"]) == {
        "2025-12-20", "2025-12-21", "2025-12-22",
    }
    assert report.dimension_summaries["regime"]["BULL"]["interpretation"] in {"DESCRIPTIVE", "LOW_SAMPLE"}
    assert report.observations == 30
    assert report.input_sha256 and report.report_sha256


def test_top_instrument_concentration_is_separately_visible() -> None:
    rows = tuple(_row(i, 0.1 if i % 6 == 0 else -0.001) for i in range(60))
    report = build_fragility_report(rows, min_group_size=4)
    assert report.top_instrument_concentration["instruments"] == 6
    assert 0 <= report.top_instrument_concentration["top_five_positive_share"] <= 1


def test_duplicate_outcomes_and_missing_dimensions_are_rejected() -> None:
    row = _row(1)
    with pytest.raises(FragilityError, match="unique"):
        build_fragility_report((row, row))
    with pytest.raises(FragilityError, match="explicit"):
        NetOutcomeSlice("x", date(2026, 1, 1), date(2025, 12, 20), "S", "", "BULL", "LOW", "1D", "BASE", "LOW", "NONE",
                        0.1, "a" * 64)
