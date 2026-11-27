from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain.forward_edge.clock import FixedClock
from financial_brain.forward_edge.data_permissions import (
    DataPermission, DataUse, PermissionStatus,
)
from financial_brain.providers.kite_stream import KiteStreamError, KiteTickerStream


NOW = datetime(2026, 11, 24, 4, 0, tzinfo=timezone.utc)


def permission(*, status=PermissionStatus.USE_ALLOWED):
    return DataPermission(
        source_id="KITE_CONNECT_READ_ONLY", use=DataUse.PAPER_SIMULATION,
        status=status, effective_from=date(2026, 11, 24) if status is PermissionStatus.USE_ALLOWED else None,
        effective_through=None,
        evidence_ref="tests/fixture-kite-paper-grant",
        reviewer="test-fixture" if status is PermissionStatus.USE_ALLOWED else None,
    )


class FakeTicker:
    MODE_FULL = "full-mode"

    def __init__(self, *_credentials):
        self.subscribed = []
        self.mode = None
        self.connected = False

    def connect(self, *, threaded):
        self.connected = threaded
        self.on_connect(self, {})

    def subscribe(self, tokens):
        self.subscribed = tokens

    def set_mode(self, mode, tokens):
        self.mode = (mode, tokens)

    def close(self):
        self.connected = False


def quote_tick(token, timestamp):
    return {
        "instrument_token": token, "mode": "full", "exchange_timestamp": timestamp,
        "last_price": 100.05, "last_traded_quantity": 2, "volume_traded": 500,
        "ohlc": {"open": 99, "high": 101, "low": 98, "close": 99.5},
        "depth": {
            "buy": [{"price": 100.0, "quantity": 20}],
            "sell": [{"price": 100.1, "quantity": 25}],
        },
    }


def test_kite_stream_subscribes_frozen_tokens_in_full_mode_and_normalizes_ticks():
    tickers = []

    def factory(*credentials):
        ticker = FakeTicker(*credentials)
        tickers.append(ticker)
        return ticker

    stream = KiteTickerStream(
        token_to_instrument={11: "NSE:INFY", 22: "NFO:INFY26NOV100CE"},
        underlying_tokens={11}, option_tokens={22}, permission=permission(),
        clock=FixedClock(NOW), ticker_factory=factory,
        credentials_provider=lambda: ("key-hidden", "token-hidden"),
    )
    stream.start()
    ticker = tickers[0]
    assert ticker.subscribed == [11, 22]
    assert ticker.mode == (ticker.MODE_FULL, [11, 22])
    ticker.on_ticks(ticker, [quote_tick(11, NOW), quote_tick(22, NOW)])
    events = stream.drain_fresh_events(decision_time=NOW + timedelta(milliseconds=20),
                                       max_quote_age=timedelta(seconds=1))
    assert [event.instrument_id for event in events] == ["NSE:INFY", "NFO:INFY26NOV100CE"]
    assert all(event.source == "KITE_CONNECT_READ_ONLY" for event in events)
    assert stream.health()["entry_readiness"] == "READY"
    assert stream.health()["evidence_quality"] == "STREAM_HEALTHY_UNATTESTED"
    assert "token-hidden" not in repr(stream.health())
    stream.stop()


def test_kite_stream_fails_closed_without_effective_paper_permission():
    with pytest.raises(KiteStreamError, match="effective Kite PAPER_SIMULATION permission"):
        KiteTickerStream(
            token_to_instrument={11: "NSE:INFY", 22: "NFO:INFY26NOV100CE"},
            underlying_tokens={11}, option_tokens={22},
            permission=permission(status=PermissionStatus.UNKNOWN_RIGHTS),
            clock=FixedClock(NOW), ticker_factory=FakeTicker,
            credentials_provider=lambda: ("key", "token"),
        )


def test_kite_stream_closes_socket_and_discards_ticks_after_permission_expiry():
    class MutableClock:
        value = NOW

        def now(self):
            return self.value

    clock = MutableClock()
    grant = DataPermission(
        source_id="KITE_CONNECT_READ_ONLY", use=DataUse.PAPER_SIMULATION,
        status=PermissionStatus.USE_ALLOWED, effective_from=date(2026, 11, 24),
        effective_through=date(2026, 11, 24), evidence_ref="tests/fixture-kite-paper-grant",
        reviewer="test-fixture",
    )
    tickers = []

    def factory(*credentials):
        ticker = FakeTicker(*credentials)
        tickers.append(ticker)
        return ticker

    stream = KiteTickerStream(
        token_to_instrument={11: "NSE:INFY", 22: "NFO:INFY26NOV100CE"},
        underlying_tokens={11}, option_tokens={22}, permission=grant,
        clock=clock, ticker_factory=factory,
        credentials_provider=lambda: ("key", "token"),
    )
    stream.start()
    assert tickers[0].connected

    # 2026-11-25 00:00 UTC is after midnight IST and beyond the grant's final date.
    clock.value = datetime(2026, 11, 25, 0, 0, tzinfo=timezone.utc)
    tickers[0].on_ticks(tickers[0], [quote_tick(11, clock.value)])

    assert not tickers[0].connected
    assert stream.drain_fresh_events(
        decision_time=clock.value, max_quote_age=timedelta(seconds=5)) == []
    health = stream.health()
    assert health["state"] == "RIGHTS_BLOCKED"
    assert health["entry_readiness"] == "NO_ENTRY"
    assert "data_permission_expired" in health["no_entry_reasons"]


def test_kite_stream_discards_stale_source_quotes_even_when_locally_receipted_now():
    stream = KiteTickerStream(
        token_to_instrument={11: "NSE:INFY", 22: "NFO:INFY26NOV100CE"},
        underlying_tokens={11}, option_tokens={22}, permission=permission(),
        clock=FixedClock(NOW), ticker_factory=FakeTicker,
        credentials_provider=lambda: ("key", "token"),
    )
    stream._on_ticks(None, [quote_tick(11, NOW - timedelta(seconds=2))])
    assert stream.drain_fresh_events(decision_time=NOW, max_quote_age=timedelta(milliseconds=500)) == []
    assert stream.health()["stale"] == 1
    assert stream.health()["entry_readiness"] == "NO_ENTRY"
    assert "stale_events_discarded" in stream.health()["no_entry_reasons"]
    assert stream.health()["evidence_quality"] == "DEGRADED"


def test_kite_stream_reconnect_or_queue_drop_forces_no_entry():
    stream = KiteTickerStream(
        token_to_instrument={11: "NSE:INFY", 22: "NFO:INFY26NOV100CE"},
        underlying_tokens={11}, option_tokens={22}, permission=permission(),
        clock=FixedClock(NOW), ticker_factory=FakeTicker,
        credentials_provider=lambda: ("key", "token"), queue_capacity=1,
    )
    stream._on_ticks(None, [quote_tick(11, NOW), quote_tick(11, NOW)])
    assert stream.health()["queue_dropped"] == 1
    assert stream.health()["entry_readiness"] == "NO_ENTRY"
    assert "queue_events_dropped" in stream.health()["no_entry_reasons"]
    stream._on_reconnect(None, 1)
    assert stream.health()["entry_readiness"] == "NO_ENTRY"
    assert "stream_state_reconnecting" in stream.health()["no_entry_reasons"]
