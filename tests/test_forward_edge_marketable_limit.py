"""FE-0057 quote-aware marketable limits avoid mid-price and depth fantasy."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from financial_brain.forward_edge.marketable_limit import plan_marketable_limit
from financial_brain.forward_edge.market_events import EventKind, MarketEvent


def _quote(bid_qty=10, ask_qty=4):
    now = datetime(2026, 11, 15, 4, 0, tzinfo=timezone.utc)
    return MarketEvent("quote-1", EventKind.QUOTE, "NSE", "NSE:INFY", now, now, now,
                       {"bid": 100, "ask": 101, "bid_quantity": bid_qty, "ask_quantity": ask_qty},
                       "fixture", sequence=1)


def test_buy_uses_ask_and_caps_to_visible_ask_quantity() -> None:
    plan = plan_marketable_limit(quote=_quote(), side="BUY", quantity=10,
                                 tick_size=Decimal("0.05"), max_slippage_bps=Decimal("10"))
    assert plan.decision == "PARTIAL"
    assert plan.executable_quantity == 4
    assert plan.limit_price == Decimal("101.15")
    assert plan.reason == "VISIBLE_SIZE_CAP"


def test_sell_uses_bid_and_rounds_limit_down_to_tick() -> None:
    plan = plan_marketable_limit(quote=_quote(), side="SELL", quantity=3,
                                 tick_size=Decimal("0.05"), max_slippage_bps=Decimal("10"))
    assert plan.decision == "READY"
    assert plan.limit_price == Decimal("99.90")
    assert plan.executable_quantity == 3


def test_zero_or_missing_visible_size_never_produces_a_fillable_quantity() -> None:
    plan = plan_marketable_limit(quote=_quote(ask_qty=0), side="BUY", quantity=1,
                                 tick_size=Decimal("0.05"), max_slippage_bps=Decimal(0))
    assert plan.decision == "NO_FILL" and plan.executable_quantity == 0
    missing = MarketEvent("q2", EventKind.QUOTE, "NSE", "NSE:INFY", _quote().event_time,
                          _quote().received_at, _quote().available_at, {"bid": 100, "ask": 101}, "fixture")
    no_size = plan_marketable_limit(quote=missing, side="BUY", quantity=1,
                                    tick_size=Decimal("0.05"), max_slippage_bps=Decimal(0))
    assert no_size.decision == "NO_FILL" and no_size.reason == "VISIBLE_SIZE_MISSING"
