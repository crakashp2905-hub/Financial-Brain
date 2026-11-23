from datetime import date
from decimal import Decimal

import pytest

from financial_brain.etf_returns import separate_returns


def test_etf_price_nav_and_underlying_tri_returns_remain_distinct() -> None:
    days = [date(2026, 1, 1), date(2026, 2, 1)]
    price, nav, benchmark = separate_returns(
        market_price=[(days[0], 100), (days[1], 110)],
        nav=[(days[0], 100), (days[1], 108)],
        underlying=[(days[0], 100), (days[1], 112)],
        benchmark_return_type="TRI",
    )
    assert [price.label, nav.label, benchmark.label] == [
        "ETF_MARKET_PRICE_RETURN", "ETF_NAV_RETURN", "UNDERLYING_TRI_RETURN",
    ]
    assert [price.return_fraction, nav.return_fraction, benchmark.return_fraction] == [
        Decimal("0.1"), Decimal("0.08"), Decimal("0.12"),
    ]


def test_price_index_is_never_mislabelled_as_total_return() -> None:
    days = [date(2026, 1, 1), date(2026, 2, 1)]
    results = separate_returns(
        market_price=[(days[0], 100), (days[1], 101)],
        nav=[(days[0], 100), (days[1], 102)],
        underlying=[(days[0], 100), (days[1], 103)],
        benchmark_return_type="PRICE",
    )
    assert results[2].label == "UNDERLYING_PRICE_RETURN"
    assert results[2].label != "UNDERLYING_TRI_RETURN"


def test_return_series_require_valid_dates_values_and_a_common_window() -> None:
    start, end = date(2026, 1, 1), date(2026, 2, 1)
    common = [(start, 100), (end, 101)]
    with pytest.raises(ValueError, match="same date window"):
        separate_returns(market_price=common, nav=common,
                         underlying=[(start, 100), (date(2026, 2, 2), 101)],
                         benchmark_return_type="TRI")
    with pytest.raises(ValueError, match="unique and ascending"):
        separate_returns(market_price=[(end, 101), (start, 100)], nav=common,
                         underlying=common, benchmark_return_type="TRI")
    with pytest.raises(ValueError, match="positive"):
        separate_returns(market_price=[(start, 0), (end, 101)], nav=common,
                         underlying=common, benchmark_return_type="TRI")
