from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.kernel.gap_exit import (
    ExecutableExitQuote, ExitQuoteSide, GapExitError, PositionDirection, resolve_gap_exit,
)


NOW = datetime(2026, 11, 21, 10, tzinfo=timezone.utc)


def _quote(**changes):
    values = {
        "bid": Decimal("94"), "ask": Decimal("94.2"), "available_quantity": Decimal("100"),
        "source_id": "kite:NSE", "source_hash": "a" * 64,
        "event_at": NOW - timedelta(milliseconds=10), "received_at": NOW - timedelta(milliseconds=5),
    }
    return ExecutableExitQuote(**(values | changes))


def _resolve(**changes):
    values = {"direction": PositionDirection.LONG, "quote": _quote(),
              "trigger_price": Decimal("97"), "quantity": Decimal("10"),
              "observed_at": NOW, "max_quote_age_ms": 100}
    return resolve_gap_exit(**(values | changes))


def test_long_gap_stop_uses_executable_bid_and_preserves_loss_beyond_stop():
    result = _resolve()
    assert result.resolved
    assert result.quote_side is ExitQuoteSide.BID
    assert result.executable_price == Decimal("94")
    assert result.trigger_price == Decimal("97")
    assert result.adverse_slippage_bps == Decimal("30000") / Decimal("97")
    assert result.quantity == Decimal("10")


def test_short_exit_uses_executable_ask_and_reports_directional_slippage():
    result = _resolve(direction=PositionDirection.SHORT, trigger_price=Decimal("103"))
    assert result.quote_side is ExitQuoteSide.ASK
    assert result.executable_price == Decimal("94.2")
    assert result.adverse_slippage_bps == 0
    assert result.signed_slippage_bps < 0


@pytest.mark.parametrize("changes, reason", [
    ({"quote": None}, "executable_quote_unavailable"),
    ({"quote": _quote(event_at=NOW - timedelta(seconds=1),
                       received_at=NOW - timedelta(seconds=1))}, "stale_or_from_future"),
    ({"quote": _quote(available_quantity=Decimal("9"))}, "insufficient_executable_quote_quantity"),
])
def test_absent_stale_or_insufficient_quote_remains_unresolved(changes, reason):
    result = _resolve(**changes)
    assert not result.resolved
    assert result.executable_price is None
    assert result.quantity == 0
    assert reason in result.reason


def test_quote_rejects_crossed_market_naive_clocks_and_bad_hash():
    with pytest.raises(GapExitError, match="ask cannot"):
        _quote(ask=Decimal("93"))
    with pytest.raises(GapExitError, match="timezone-aware"):
        _quote(event_at=datetime(2026, 11, 21, 10))
    with pytest.raises(GapExitError, match="SHA-256"):
        _quote(source_hash="bad")
