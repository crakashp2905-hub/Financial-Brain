import pytest

from financial_brain.mf_lookthrough import LookthroughError, lookthrough, overlap


def test_nested_fof_consolidates_duplicate_underlying_stock_once_per_weight_path():
    data = {
        "FOF": [{"underlying_scheme_code": "A", "weight_pct": 50},
                {"underlying_scheme_code": "B", "weight_pct": 50}],
        "A": [{"instrument_id": "INE1", "asset_type": "EQUITY", "weight_pct": 60}],
        "B": [{"instrument_id": "INE1", "asset_type": "EQUITY", "weight_pct": 30},
              {"instrument_id": "INE2", "asset_type": "EQUITY", "weight_pct": 50}],
    }
    result = lookthrough("FOF", data)
    assert result.exposures == {"INE1": 45.0, "INE2": 25.0}
    assert result.expanded_weight_pct == 70.0
    assert result.opaque_weight_pct == 30.0
    assert result.coverage_pct == 70.0


def test_cycle_or_missing_data_is_reported_as_opaque_and_overlap_is_coverage_adjusted():
    left = lookthrough("A", {"A": [{"underlying_scheme_code": "B", "weight_pct": 100}],
                               "B": [{"underlying_scheme_code": "A", "weight_pct": 100}]})
    right = lookthrough("C", {"C": [{"instrument_id": "X", "asset_type": "EQUITY",
                                      "weight_pct": 50}]})
    assert left.coverage_pct == 0
    assert left.opaque_weight_pct == 100
    assert overlap(left, right)["coverage_adjusted_overlap_pct"] == 0


@pytest.mark.parametrize("rows", [
    [{"instrument_id": "X", "asset_type": "EQUITY", "weight_pct": 101}],
    [{"instrument_id": "X", "asset_type": "EQUITY", "weight_pct": 70},
     {"instrument_id": "Y", "asset_type": "EQUITY", "weight_pct": 40}],
])
def test_invalid_or_overallocated_portfolios_rejected(rows):
    with pytest.raises(LookthroughError):
        lookthrough("A", {"A": rows})
