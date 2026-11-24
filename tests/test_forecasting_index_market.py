from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from financial_brain.forecasting.index_market import (
    IndexCatalogue,
    IndexMarketError,
    IndexMinute,
    IndexRecord,
    OptionableIndexRecord,
    build_15m_bars,
    direction_label,
    forward_return,
)

IST = ZoneInfo("Asia/Kolkata")
DAY = date(2026, 11, 25)


def sample_index(**kwargs) -> IndexRecord:
    values = {"canonical_id": "NIFTY50", "name": "Nifty 50", "provider_symbol": "NIFTY 50",
              "provider_token": 256265, "exchange": "NSE", "family": "BROAD_MARKET",
              "publication_clock": "Asia/Kolkata", "valid_from": date(2010, 1, 1)}
    return IndexRecord(**(values | kwargs))


def minute_rows(count: int, *, start_minute: int = 15, base: float = 25000.0,
                delayed_last: bool = False) -> list[IndexMinute]:
    start = datetime(2026, 11, 25, 9, start_minute, tzinfo=IST)
    rows = []
    for n in range(count):
        ts = start + timedelta(minutes=n)
        px = base + n
        available = ts + timedelta(seconds=2)
        if delayed_last and n == count - 1:
            available += timedelta(minutes=1)
        rows.append(IndexMinute(ts, available, px, px + 1, px - 1, px + 0.5))
    return rows


def test_catalogue_separates_index_universe_from_optionable_indices() -> None:
    option = OptionableIndexRecord("NIFTY50", "NIFTY", "NFO", "a" * 64, date(2026, 1, 1))
    catalogue = IndexCatalogue([sample_index()], [option])
    assert catalogue.index_at("NIFTY50", DAY).provider_token == 256265
    assert catalogue.is_optionable_at("NIFTY50", DAY)
    assert not catalogue.is_optionable_at("NIFTY50", date(2025, 1, 1))


def test_catalogue_rejects_overlapping_effective_ranges() -> None:
    old = sample_index(valid_to=date(2026, 6, 30))
    new = sample_index(name="Renamed", valid_from=date(2026, 6, 1))
    with pytest.raises(IndexMarketError, match="overlap"):
        IndexCatalogue([old, new], [])


def test_15m_index_bars_require_complete_minutes_and_available_cutoff() -> None:
    complete = minute_rows(15)
    incomplete = minute_rows(14, start_minute=30)
    delayed = minute_rows(15, start_minute=45, delayed_last=True)
    bars = build_15m_bars(complete + incomplete + delayed,
                          as_of=datetime(2026, 11, 25, 10, 0, tzinfo=IST))
    assert len(bars) == 1
    assert bars[0].start == datetime(2026, 11, 25, 9, 15, tzinfo=IST)
    assert bars[0].end == datetime(2026, 11, 25, 9, 30, tzinfo=IST)
    assert bars[0].minute_count == 15


def test_15m_cutoff_never_emits_still_open_bar() -> None:
    with_open_bar = minute_rows(15) + minute_rows(15, start_minute=30)
    bars = build_15m_bars(with_open_bar, as_of=datetime(2026, 11, 25, 9, 44, tzinfo=IST))
    assert len(bars) == 1


def test_forward_return_and_frozen_noise_labels() -> None:
    bars = build_15m_bars(minute_rows(45), as_of=datetime(2026, 11, 25, 10, 0, tzinfo=IST))
    assert forward_return(bars, 0) > 0
    assert direction_label(0.002, noise_threshold=0.001) == "UP"
    assert direction_label(-0.002, noise_threshold=0.001) == "DOWN"
    assert direction_label(0.0005, noise_threshold=0.001) == "FLAT"


def test_invalid_or_noncontiguous_label_inputs_fail_closed() -> None:
    bars = build_15m_bars(minute_rows(45), as_of=datetime(2026, 11, 25, 10, 0, tzinfo=IST))
    with pytest.raises(IndexMarketError, match="positive frozen"):
        direction_label(0.1, noise_threshold=0)
    with pytest.raises(IndexMarketError, match="contiguous"):
        forward_return([bars[0], bars[2]], 0)
