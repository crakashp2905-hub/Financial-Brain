import pytest

from financial_brain.strategies.evaluation_controls import (
    Candidate, matched_sector_size_controls,
)


def test_sector_size_controls_match_same_sector_and_do_not_reuse_controls():
    events = [Candidate("A", "IT", 0.20, 0.95, 0.02, 1000),
              Candidate("B", "IT", 0.10, 0.90, 0.03, 900)]
    pool = [Candidate("A", "IT", 0.20, 0.95, 0.02, 1000),
            Candidate("C", "IT", 0.19, 0.94, 0.021, 980),
            Candidate("D", "IT", 0.11, 0.91, 0.029, 890),
            Candidate("E", "BANK", 0.20, 0.95, 0.02, 1000)]
    matches = matched_sector_size_controls(events, pool)
    assert [row["control"] for row in matches] == ["C", "D"]
    assert all(row["sector"] == "IT" for row in matches)
    assert all("log_market_cap" in row["matched_covariates"] for row in matches)


def test_sector_size_matching_fails_closed_without_point_in_time_size():
    event = Candidate("A", "IT", 0.2, 0.95, 0.02, 1000)
    missing_size = Candidate("B", "IT", 0.1, 0.9, 0.03)
    with pytest.raises(ValueError, match="market cap"):
        matched_sector_size_controls([event], [missing_size])
