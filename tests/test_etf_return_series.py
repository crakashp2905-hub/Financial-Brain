from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from financial_brain.features.etf_prices import separate_etf_return_series
from financial_brain.providers.etf_reference import ETFValuationObservation


def _nav(day: date, value: str) -> ETFValuationObservation:
    published = datetime(day.year, day.month, day.day, 10, tzinfo=timezone.utc)
    return ETFValuationObservation(
        source_id="AMC", exchange="NSE", symbol="INDEXETF", isin="INE000A01001",
        valuation_type="NAV", valuation_date=day, value_per_unit=value,
        published_at=published, observed_at=published,
        source_ref="NAV statement", source_sha256="a" * 64,
    )


def test_price_nav_and_underlying_tri_returns_remain_separate_and_do_not_bridge_gaps() -> None:
    days = [date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 12)]
    output = separate_etf_return_series(
        expected_sessions=days,
        etf_price_close={days[0]: 100, days[2]: 102},
        nav_observations=[_nav(day, str(100 + index)) for index, day in enumerate(days)],
        underlying_total_return_index={day: float(200 + 2 * index)
                                       for index, day in enumerate(days)},
    )
    assert output["ETF_PRICE_RETURN"][days[0].isoformat()] is None
    assert output["ETF_PRICE_RETURN"][days[1].isoformat()] is None
    assert output["ETF_PRICE_RETURN"][days[2].isoformat()] is None
    assert output["ETF_NAV_RETURN"][days[1].isoformat()] == pytest.approx(0.01)
    assert output["UNDERLYING_TOTAL_RETURN_INDEX"][days[1].isoformat()] == pytest.approx(0.01)
    assert output["methodology"]["price_and_nav_distributions_reinvested"] is False


def test_return_series_reject_duplicate_nav_revisions_and_inav_inputs() -> None:
    day = date(2026, 11, 10)
    nav = _nav(day, "100")
    with pytest.raises(ValueError, match="resolve NAV revisions"):
        separate_etf_return_series(expected_sessions=[day], etf_price_close={day: 100},
                                   nav_observations=[nav, nav], underlying_total_return_index={day: 200})
    inav = ETFValuationObservation(
        source_id="NSE", exchange="NSE", symbol="INDEXETF", isin="INE000A01001",
        valuation_type="INAV", valuation_date=day, value_per_unit="100",
        published_at=nav.published_at, observed_at=nav.observed_at,
        source_ref="iNAV", source_sha256="b" * 64,
    )
    with pytest.raises(ValueError, match="NAV observations only"):
        separate_etf_return_series(expected_sessions=[day], etf_price_close={day: 100},
                                   nav_observations=[inav], underlying_total_return_index={day: 200})
