from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.evaluation.intraday_book_coverage import (
    CoverageInputError,
    RequiredInterval,
    assess_sub30_executable_coverage,
)
from financial_brain.forward_edge.market_data import NormalizedEvent


T0 = datetime(2026, 11, 10, 4, 0, tzinfo=timezone.utc)


def _quote(at: datetime, **overrides: str) -> NormalizedEvent:
    payload = {
        "source": "kite",
        "exchange": "NSE",
        "bid_price": "100.00",
        "ask_price": "100.10",
        "bid_qty": "50",
        "ask_qty": "40",
    }
    payload.update(overrides)
    return NormalizedEvent(
        "QUOTE", "NSE:INFY", at - timedelta(seconds=1),
        at - timedelta(milliseconds=200), at - timedelta(milliseconds=100),
        tuple(sorted(payload.items())),
    )


def _intervals() -> list[RequiredInterval]:
    return [
        RequiredInterval("entry-1", "NSE:INFY", T0),
        RequiredInterval("exit-1", "NSE:INFY", T0 + timedelta(seconds=10)),
    ]


def test_ltp_only_interval_blocks_the_entire_executable_alpha_cohort() -> None:
    ltp = _quote(T0 + timedelta(seconds=10), bid_price="", ask_price="")
    report = assess_sub30_executable_coverage(
        _intervals(), [_quote(T0), ltp], interval_seconds=60,
    )
    assert report.status == "BLOCKED_INSUFFICIENT_ORDER_BOOK_COVERAGE"
    assert report.allow_executable_alpha is False
    assert report.covered_intervals == 1
    assert report.required_intervals == 2
    assert "MISSING_OR_INVALID_BOOK" in report.interval_evidence[1].reasons


def test_complete_fresh_provenance_and_depth_is_eligible() -> None:
    report = assess_sub30_executable_coverage(
        _intervals(), [_quote(T0), _quote(T0 + timedelta(seconds=10))], interval_seconds=60,
    )
    assert report.status == "ELIGIBLE_FOR_EXECUTABLE_ALPHA_TEST"
    assert report.allow_executable_alpha is True
    assert report.coverage_pct == 100.0
    assert all(item.quote_sha256 for item in report.interval_evidence)
    assert all(item.spread_bps is not None for item in report.interval_evidence)


def test_missing_source_provenance_is_not_executable_evidence() -> None:
    quote = _quote(T0, source="")
    report = assess_sub30_executable_coverage(
        [_intervals()[0]], [quote], interval_seconds=60,
    )
    assert not report.allow_executable_alpha
    assert "MISSING_SOURCE_PROVENANCE" in report.interval_evidence[0].reasons


def test_stale_or_wide_quote_blocks_its_interval() -> None:
    stale = _quote(T0, bid_price="90", ask_price="110")
    report = assess_sub30_executable_coverage(
        [_intervals()[0]], [stale], interval_seconds=60, max_spread_bps=50,
    )
    assert not report.allow_executable_alpha
    assert "WIDE_SPREAD" in report.interval_evidence[0].reasons


def test_empty_or_non_sub30_coverage_request_is_rejected() -> None:
    with pytest.raises(CoverageInputError):
        assess_sub30_executable_coverage([], [], interval_seconds=60)
    with pytest.raises(CoverageInputError):
        assess_sub30_executable_coverage(_intervals(), [], interval_seconds=1800)


def test_coverage_digest_is_deterministic() -> None:
    quotes = [_quote(T0), _quote(T0 + timedelta(seconds=10))]
    first = assess_sub30_executable_coverage(_intervals(), quotes, interval_seconds=60)
    second = assess_sub30_executable_coverage(_intervals(), quotes, interval_seconds=60)
    assert first.evidence_sha256 == second.evidence_sha256
