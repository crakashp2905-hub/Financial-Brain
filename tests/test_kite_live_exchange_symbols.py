from __future__ import annotations

from datetime import datetime, timezone

import pytest

from financial_brain.ingest.kite_live import forward_events


def _quote() -> dict:
    return {
        "timestamp": "2026-11-13 10:15:00",
        "last_trade_time": "2026-11-13 10:14:59",
        "last_price": 101.5,
        "volume": 1000,
        "depth": {
            "buy": [{"price": 101.4, "quantity": 12}],
            "sell": [{"price": 101.6, "quantity": 9}],
        },
    }


def test_read_only_kite_events_accept_nse_default_and_explicit_bse_symbols() -> None:
    requested: list[str] = []

    def fake_quote(keys: list[str], mode: str) -> dict:
        assert mode == "full"
        requested.extend(keys)
        return {key: _quote() for key in keys}

    events = forward_events(
        ["INFY", "bse:INDEXETF"],
        quote_fn=fake_quote,
        received_at=datetime(2026, 11, 13, 4, 45, 1, tzinfo=timezone.utc),
    )
    assert requested == ["NSE:INFY", "BSE:INDEXETF"]
    assert [(event.venue, event.instrument_id) for event in events] == [
        ("NSE", "NSE:INFY"), ("BSE", "BSE:INDEXETF")
    ]
    assert all(event.payload["bid"] < event.payload["ask"] for event in events)


@pytest.mark.parametrize("bad", ["MCX:GOLD", "NSE:", "BSE:NSE:ETF"])
def test_read_only_kite_events_reject_unsupported_or_malformed_exchange_keys(bad: str) -> None:
    with pytest.raises(ValueError, match="qualified symbols"):
        forward_events([bad], quote_fn=lambda *_: {})


def test_exchange_qualified_duplicates_are_rejected_after_normalization() -> None:
    with pytest.raises(ValueError, match="must be unique"):
        forward_events(["ETF", "nse:ETF"], quote_fn=lambda *_: {})
