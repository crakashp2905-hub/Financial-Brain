from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from financial_brain.features.etf_pricing import resolve_etf_paper_price
from financial_brain.forward_edge.market_data import normalize_kite_quote


UTC = timezone.utc
NOW = datetime(2026, 11, 13, 4, 45, 2, tzinfo=UTC)


def _quote(*, timestamp="2026-11-13 10:15:00", received=NOW):
    return normalize_kite_quote(
        "BSE:INDEXETF",
        {"timestamp": timestamp, "last_price": 101.5, "volume": 100,
         "depth": {"buy": [{"price": 101.4, "quantity": 12}],
                   "sell": [{"price": 101.6, "quantity": 9}]}},
        received_at=received,
    )


def test_missing_quote_is_explicitly_unpriced_and_cannot_emit_a_fill() -> None:
    result = resolve_etf_paper_price(None, venue="BSE", instrument_id="BSE:INDEXETF",
                                     decision_time=NOW, max_quote_age=timedelta(seconds=5))
    assert result == {
        "fill_status": "UNPRICED", "price_state": "UNPRICED", "price_source": None,
        "bid": None, "ask": None, "reason": "NO_OBSERVED_QUOTE", "fill_created": False,
    }


def test_fresh_executable_quote_is_the_only_price_source() -> None:
    result = resolve_etf_paper_price(_quote(), venue="BSE", instrument_id="BSE:INDEXETF",
                                     decision_time=NOW, max_quote_age=timedelta(seconds=5))
    assert result["fill_status"] == "QUOTE_PRICED"
    assert result["bid"] == "101.4" and result["ask"] == "101.6"
    assert result["price_source"] == "KITE_CONNECT_READ_ONLY"


def test_stale_ltp_only_and_wrong_identity_quotes_remain_unpriced() -> None:
    stale = _quote(received=NOW + timedelta(seconds=20))
    assert resolve_etf_paper_price(stale, venue="BSE", instrument_id="BSE:INDEXETF",
                                   decision_time=NOW + timedelta(seconds=21),
                                   max_quote_age=timedelta(seconds=5))["reason"] == "STALE_QUOTE"

    tick = normalize_kite_quote("BSE:INDEXETF", {"timestamp": "2026-11-13 10:15:00",
        "last_price": 101.5, "volume": 100}, received_at=NOW)
    assert resolve_etf_paper_price(tick, venue="BSE", instrument_id="BSE:INDEXETF",
                                   decision_time=NOW, max_quote_age=timedelta(seconds=5))["reason"] == \
        "NO_EXECUTABLE_TWO_SIDED_DEPTH"
    assert resolve_etf_paper_price(_quote(), venue="BSE", instrument_id="BSE:OTHERETF",
                                   decision_time=NOW, max_quote_age=timedelta(seconds=5))["reason"] == \
        "QUOTE_IDENTITY_MISMATCH"


@pytest.mark.parametrize("venue,instrument", [("NSE", "BSE:ETF"), ("MCX", "MCX:GOLD")])
def test_unsupported_venue_identity_is_rejected(venue: str, instrument: str) -> None:
    with pytest.raises(ValueError, match="matching NSE/BSE"):
        resolve_etf_paper_price(None, venue=venue, instrument_id=instrument,
                                decision_time=NOW, max_quote_age=timedelta(seconds=5))
