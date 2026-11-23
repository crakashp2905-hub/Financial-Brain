from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import math

import pytest

from financial_brain.features.etf_momentum import HORIZONS_SESSIONS, rank_etf_momentum
from financial_brain.providers.etf_reference import ETFExposureCategory, ETFReferenceRecord


def _record(symbol: str, isin: str, *, index: str = "NIFTY 50 TRI",
            exposure: ETFExposureCategory = ETFExposureCategory.EQUITY_BROAD_MARKET,
            published_at: datetime | None = None) -> ETFReferenceRecord:
    published = published_at or datetime(2025, 1, 1, tzinfo=timezone.utc)
    return ETFReferenceRecord(
        source_id="NSE", exchange="NSE", symbol=symbol, isin=isin,
        listing_date=date(2020, 1, 1), effective_from=date(2020, 1, 1), effective_to=None,
        series="EQ", published_at=published, observed_at=published + timedelta(minutes=1),
        source_ref="synthetic test reference", source_sha256="a" * 64,
        underlying_index=index, exposure_category=exposure,
    )


def _prices(sessions: list[date], growth: float) -> dict[date, float]:
    return {day: 100.0 * (1 + growth) ** (i / 252) for i, day in enumerate(sessions)}


def _hashes(isins: list[str], sessions: list[date]) -> dict[str, dict[date, str]]:
    return {isin: {day: (f"{index + 1:064x}") for index, day in enumerate(sessions)} for isin in isins}


def test_fixed_horizon_rank_is_deterministic_and_within_comparable_exposure() -> None:
    sessions = [date(2025, 1, 1) + timedelta(days=i) for i in range(260)]
    as_of = datetime.combine(sessions[-1], time(23, 59), timezone.utc)
    records = [
        _record("ETFLOW", "INE000A01001"),
        _record("ETFMID", "INE000A01002"),
        _record("ETFHI", "INE000A01003"),
        _record("GOLDFUND", "INE000A01004", index="GOLD TRI",
                exposure=ETFExposureCategory.GOLD),
    ]
    prices = {
        "INE000A01001": _prices(sessions, 0.02),
        "INE000A01002": _prices(sessions, 0.08),
        "INE000A01003": _prices(sessions, 0.15),
        "INE000A01004": _prices(sessions, 0.30),
    }

    hashes = _hashes(list(prices), sessions)
    ranked = rank_etf_momentum(as_of=as_of, expected_sessions=sessions,
                               closes_by_isin=prices, source_hashes_by_isin=hashes,
                               references=records)

    assert [row.symbol for row in ranked] == ["ETFHI", "ETFMID", "ETFLOW"]
    assert {row.underlying_index for row in ranked} == {"NIFTY 50 TRI"}
    assert all(row.composite_percentile == 1.0 - i / 2 for i, row in enumerate(ranked))
    assert all(tuple(h for h, _ in row.horizon_returns) == HORIZONS_SESSIONS for row in ranked)
    assert all(len(row.strategy_spec_sha256) == len(row.price_input_sha256) == 64 for row in ranked)
    assert rank_etf_momentum(as_of=as_of, expected_sessions=sessions,
                             closes_by_isin=prices, source_hashes_by_isin=hashes,
                             references=records) == ranked


def test_missing_horizon_endpoint_and_not_yet_observed_reference_are_excluded() -> None:
    sessions = [date(2025, 1, 1) + timedelta(days=i) for i in range(260)]
    as_of = datetime.combine(sessions[-1], time(23, 59), timezone.utc)
    late = _record("LATE", "INE000A01003", published_at=as_of + timedelta(seconds=1))
    records = [_record("ONE", "INE000A01001"), _record("TWO", "INE000A01002"),
               _record("THREE", "INE000A01004"), late]
    prices = {row.isin: _prices(sessions, 0.05 + i * 0.02) for i, row in enumerate(records)}
    del prices["INE000A01001"][sessions[-1 - HORIZONS_SESSIONS[0]]]

    ranked = rank_etf_momentum(as_of=as_of, expected_sessions=sessions,
                               closes_by_isin=prices,
                               source_hashes_by_isin=_hashes(list(prices), sessions),
                               references=records)

    assert [row.symbol for row in ranked] == ["THREE", "TWO"]

    hashes = _hashes(list(prices), sessions)
    del hashes["INE000A01002"][sessions[-1 - HORIZONS_SESSIONS[-1]]]
    assert rank_etf_momentum(as_of=as_of, expected_sessions=sessions,
                             closes_by_isin=prices, source_hashes_by_isin=hashes,
                             references=records) == []


def test_unverified_and_stale_reference_exposure_categories_do_not_form_a_cohort() -> None:
    sessions = [date(2025, 1, 1) + timedelta(days=i) for i in range(260)]
    as_of = datetime.combine(sessions[-1], time(23, 59), timezone.utc)
    records = [
        _record("UNKNOWN", "INE000A01001", exposure=ETFExposureCategory.UNKNOWN),
        _record("OTHER_INDEX", "INE000A01002", index="NIFTY BANK TRI"),
    ]
    prices = {row.isin: _prices(sessions, 0.1) for row in records}

    assert rank_etf_momentum(as_of=as_of, expected_sessions=sessions,
                             closes_by_isin=prices, source_hashes_by_isin=_hashes(list(prices), sessions),
                             references=records) == []


def test_invalid_prices_and_future_sessions_fail_closed() -> None:
    sessions = [date(2025, 1, 1) + timedelta(days=i) for i in range(260)]
    as_of = datetime.combine(sessions[-1], time(23, 59), timezone.utc)
    records = [_record("ONE", "INE000A01001"), _record("TWO", "INE000A01002")]
    prices = {row.isin: _prices(sessions, 0.1) for row in records}
    prices["INE000A01001"][sessions[-1]] = math.nan
    with pytest.raises(ValueError, match="finite positive"):
        rank_etf_momentum(as_of=as_of, expected_sessions=sessions,
                          closes_by_isin=prices, source_hashes_by_isin=_hashes(list(prices), sessions),
                          references=records)

    with_future = sessions + [sessions[-1] + timedelta(days=1)]
    with pytest.raises(ValueError, match="cannot follow as_of"):
        rank_etf_momentum(as_of=as_of, expected_sessions=with_future,
                          closes_by_isin={}, source_hashes_by_isin={}, references=[])
