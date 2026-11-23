from __future__ import annotations
from datetime import date, datetime, time, timedelta, timezone
import pytest
from financial_brain.features.etf_bars import aggregate_session_bars
from financial_brain.forward_edge.market_events import EventKind, MarketEvent
from financial_brain.forward_edge.venue_calendar import CalendarError, Session, VenueCalendar
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from financial_brain.etf_bars import completed_bars
DAY = date(2026, 11, 13)
UTC = timezone.utc

def _calendar() -> VenueCalendar:
    return VenueCalendar([Session('BSE', DAY, time(9, 15), time(15, 30), 'REGULAR', 'fixture', 'a' * 64, datetime(2026, 11, 13, 4, tzinfo=UTC))], source_as_of=DAY)

def _tick(event_id: str, hour: int, minute: int, second: int, price: float, volume: int, *, available: tuple[int, int, int] | None=None, venue: str='BSE', instrument: str='BSE:INDEXETF') -> MarketEvent:
    event_time = datetime(2026, 11, 13, hour, minute, second, tzinfo=UTC)
    received = datetime(2026, 11, 13, *available, tzinfo=UTC) if available is not None else event_time + timedelta(seconds=1)
    return MarketEvent(event_id, EventKind.TICK, venue, instrument, event_time, received, received, {'price': price, 'volume': volume}, 'KITE_CONNECT_READ_ONLY')

def test_aggregator_uses_sourced_session_buckets_and_as_of_availability() -> None:
    events = [_tick('preopen', 3, 44, 59, 999, 1), _tick('one', 3, 45, 30, 100, 10, available=(3, 45, 31)), _tick('two', 3, 46, 30, 102, 15, available=(3, 46, 31)), _tick('three', 3, 51, 0, 99, 40, available=(3, 51, 1)), _tick('future', 3, 52, 0, 200, 100, available=(3, 53, 0)), _tick('after-close', 10, 30, 0, 300, 200), _tick('other-venue', 3, 47, 0, 5, 1, venue='NSE', instrument='NSE:INDEXETF')]
    bars = aggregate_session_bars(events, venue='BSE', instrument_id='BSE:INDEXETF', session_date=DAY, calendar=_calendar(), interval_minutes=5, as_of=datetime(2026, 11, 13, 3, 52, tzinfo=UTC))
    assert len(bars) == 2
    assert (bars[0]['open'], bars[0]['high'], bars[0]['low'], bars[0]['close']) == (100, 102, 100, 102)
    assert bars[0]['volume'] == 5
    assert bars[1]['close'] == 99 and bars[1]['volume'] == 25
    assert bars[1]['available_at'] == '2026-11-13T03:51:01+00:00'
    assert bars[0]['bucket_start'] == '2026-11-13T03:45:00+00:00'

def test_missing_sourced_exchange_session_fails_closed() -> None:
    empty = VenueCalendar([], source_as_of=DAY)
    with pytest.raises(CalendarError, match='no sourced session'):
        aggregate_session_bars([], venue='NSE', instrument_id='NSE:ETF', session_date=DAY, calendar=empty, interval_minutes=5, as_of=datetime(2026, 11, 13, 3, 50, tzinfo=UTC))

def test_unsupported_exchange_or_interval_is_rejected() -> None:
    with pytest.raises(ValueError, match='qualified'):
        aggregate_session_bars([], venue='NSE', instrument_id='BSE:ETF', session_date=DAY, calendar=_calendar(), interval_minutes=5, as_of=datetime(2026, 11, 13, 3, 50, tzinfo=UTC))
    with pytest.raises(ValueError, match='interval_minutes'):
        aggregate_session_bars([], venue='BSE', instrument_id='BSE:ETF', session_date=DAY, calendar=_calendar(), interval_minutes=3, as_of=datetime(2026, 11, 13, 3, 50, tzinfo=UTC))
IST = ZoneInfo('Asia/Kolkata')

def _event(minute: int, price: float, volume: int) -> MarketEvent:
    stamp = datetime(2026, 11, 23, 9, minute, tzinfo=IST)
    return MarketEvent(event_id=f'quote-{minute}', kind=EventKind.QUOTE, venue='NSE', instrument_id='NSE:ETFONE', event_time=stamp, received_at=stamp + timedelta(seconds=1), available_at=stamp + timedelta(seconds=1), source='KITE_CONNECT_READ_ONLY', payload={'bid': price - 0.01, 'ask': price + 0.01, 'last_price': price, 'volume': volume})

def test_bars_use_only_complete_nse_session_minutes_and_preserve_volume_delta() -> None:
    events = [_event(m, 100 + m, 10 * (m - 14)) for m in range(15, 20)]
    bars = completed_bars(events, as_of=datetime(2026, 11, 23, 9, 21, tzinfo=IST), minutes=5)
    assert len(bars) == 1
    bar = bars[0]
    assert bar.start == datetime(2026, 11, 23, 9, 15, tzinfo=IST)
    assert (bar.open, bar.high, bar.low, bar.close) == (115, 119, 115, 119)
    assert bar.volume_change == 50 and bar.depth_coverage == 1.0 and bar.complete

def test_bars_do_not_emit_partial_or_gap_filled_windows() -> None:
    partial = [_event(m, 100 + m, 10 * (m - 14)) for m in range(15, 19)]
    before_close = completed_bars(partial, as_of=datetime(2026, 11, 23, 9, 20, tzinfo=IST), minutes=5)
    with_gap = completed_bars(partial, as_of=datetime(2026, 11, 23, 9, 21, tzinfo=IST), minutes=5)
    assert before_close == []
    assert with_gap == []
