import pandas as pd
import pytest

from financial_brain.research.cross_market import align, correlation_diagnostic


def _frames():
    us = pd.DataFrame([
        {"proxy": "US_TECH", "available_at": "2026-10-28T20:00:00Z", "return": .02},
        {"proxy": "US_TECH", "available_at": "2026-10-29T20:00:00Z", "return": -.03},
    ])
    india = pd.DataFrame([
        {"ticker": "TEST", "decision_at": "2026-10-29T03:30:00Z"},
        {"ticker": "TEST", "decision_at": "2026-10-30T03:30:00Z"},
    ])
    mapping = pd.DataFrame([{"ticker": "TEST", "domain": "IT_SERVICES",
                             "proxy": "US_TECH", "weight": .6,
                             "valid_from": "2026-01-01T00:00:00Z",
                             "valid_to": "2026-12-31T23:59:59Z"}])
    return us, india, mapping


def test_us_close_is_used_only_after_available():
    out = align(*_frames())
    assert out["us_return"].tolist() == [.02, -.03]
    assert (out["age_hours"] > 0).all()


def test_stale_or_undated_mapping_cannot_become_feature():
    us, india, mapping = _frames()
    assert align(us, india, mapping, max_age_hours=1)["us_return"].isna().all()
    mapping["valid_to"] = "2026-10-28T00:00:00Z"
    assert align(us, india, mapping).empty
    mapping["weight"] = 1.5
    with pytest.raises(ValueError, match="weights"):
        align(us, india, mapping)


def test_correlation_is_descriptive_not_a_signal():
    us, india, mapping = _frames()
    aligned = align(us, india, mapping)
    outcomes = india.assign(next_return=[.01, -.02])
    report = correlation_diagnostic(aligned, outcomes)
    assert report.iloc[0]["status"] == "INSUFFICIENT_PAIRS"
    assert pd.isna(report.iloc[0]["correlation"])
