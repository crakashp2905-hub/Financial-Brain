from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from financial_brain.etf_bars import completed_bars
from financial_brain.forward_edge.market_events import EventKind, MarketEvent

IST = ZoneInfo("Asia/Kolkata")


def _event(minute: int, price: float, volume: int) -> MarketEvent:
    stamp = datetime(2026, 11, 23, 9, minute, tzinfo=IST)
    return MarketEvent(
        event_id=f"quote-{minute}", kind=EventKind.QUOTE, venue="NSE",
        instrument_id="NSE:ETFONE", event_time=stamp, received_at=stamp + timedelta(seconds=1),
        available_at=stamp + timedelta(seconds=1), source="KITE_CONNECT_READ_ONLY",
        payload={"bid": price - 0.01, "ask": price + 0.01, "last_price": price,
                 "volume": volume},
    )


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
    before_close = completed_bars(
        partial, as_of=datetime(2026, 11, 23, 9, 20, tzinfo=IST), minutes=5)
    with_gap = completed_bars(
        partial, as_of=datetime(2026, 11, 23, 9, 21, tzinfo=IST), minutes=5)
    assert before_close == []
    assert with_gap == []
