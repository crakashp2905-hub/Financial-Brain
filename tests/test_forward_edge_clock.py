from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.clock import (
    ClockError,
    FixedClock,
    capture_market_clocks,
)
from financial_brain.paper.intraday_runner import run_live


def test_market_clock_uses_source_exchange_time_and_one_injected_receipt_read():
    exchange = datetime(2026, 11, 13, 4, 45, tzinfo=timezone.utc)
    receipt = datetime(2026, 11, 13, 4, 45, 1, tzinfo=timezone.utc)
    clocks = capture_market_clocks(exchange_at=exchange, provider=FixedClock(receipt),
                                   availability_delay=timedelta(milliseconds=25))
    assert clocks.exchange_at == exchange
    assert clocks.received_at == receipt
    assert clocks.available_at == receipt + timedelta(milliseconds=25)


def test_market_clock_rejects_availability_before_receipt():
    with pytest.raises(ClockError, match="cannot be negative"):
        capture_market_clocks(exchange_at=datetime.now(timezone.utc),
                              provider=FixedClock(datetime.now(timezone.utc)),
                              availability_delay=timedelta(seconds=-1))


def test_live_runner_does_not_accept_two_competing_clock_sources():
    fixed = FixedClock(datetime(2026, 11, 13, 4, 45, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="either clock or legacy now_fn"):
        run_live(clock=fixed, now_fn=lambda: fixed.now())
