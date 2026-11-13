"""Adversarial quote perturbations for the paper-only marketable-fill adapter."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from financial_brain.forward_edge.market_data import NormalizedEvent
from financial_brain.forward_edge.paper_execution_adapter import MarketableQuoteSimulator
from financial_brain.forward_edge.execution import OrderIntent, passive_limit


DECISION = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)


def _quote(offset_ms, *, bid="99.95", ask="100.05", bid_qty=10, ask_qty=10):
    available = DECISION + timedelta(milliseconds=offset_ms)
    return NormalizedEvent(
        "QUOTE", "NSE:INFY", available - timedelta(milliseconds=2),
        available - timedelta(milliseconds=1), available,
        tuple(sorted({"bid_price": bid, "ask_price": ask,
                      "bid_qty": str(bid_qty), "ask_qty": str(ask_qty)}.items())),
    )


def _simulate(events, *, latency=timedelta(milliseconds=10), quantity=5, feed_health=None):
    adapter = MarketableQuoteSimulator({"NSE:INFY": Decimal("0.05")}, latency=latency,
                                       fee_schedule_sha256="a" * 64,
                                       feed_health=feed_health)
    return adapter({"instrument": "NSE:INFY", "side": "BUY", "quantity": quantity,
                    "decision_at_utc": DECISION.isoformat()}, "perturbation", events)


def test_wider_spread_and_price_gap_never_improve_buy_fill_price():
    baseline = _simulate([_quote(11, ask="100.05")])
    stressed = _simulate([_quote(11, bid="98.00", ask="103.00")])
    assert baseline["status"] == "FILLED"
    assert stressed["status"] == "NO_TRADE"
    assert stressed["fills"] == []
    assert stressed["quote_quality_rejections"][0]["reason_codes"] == ["WIDE_SPREAD"]


def test_feed_gap_and_late_pre_cutoff_quote_remain_missed():
    assert _simulate([])["status"] == "MISSED"
    assert _simulate([_quote(9)])["status"] == "MISSED"


def test_missing_or_zero_depth_never_creates_a_fill():
    missing = NormalizedEvent("QUOTE", "NSE:INFY", DECISION, DECISION, DECISION,
                              (("last_price", "100"),))
    zero_size = _quote(11, ask_qty=0)
    receipt = _simulate([missing, zero_size])
    assert receipt["status"] == "NO_TRADE"
    assert receipt["fills"] == []


def test_duplicate_quote_print_does_not_multiply_visible_size_or_fill():
    duplicate = _quote(11, ask_qty=3)
    receipt = _simulate([duplicate, duplicate], quantity=5)
    assert receipt["status"] == "PARTIAL"
    assert receipt["fills"][0]["quantity"] == 3


def test_duplicate_trade_print_does_not_inflate_passive_order_fill_volume():
    print_at = DECISION + timedelta(milliseconds=20)
    intent = OrderIntent("NSE:INFY", "BUY", 2, DECISION, Decimal("0.05"), Decimal("100"))
    duplicate = (print_at, Decimal("99"), 1)
    result = passive_limit(intent, [duplicate, duplicate], timedelta(milliseconds=10))
    assert result.status == "PARTIAL"
    assert result.filled_qty == 1
    assert result.queue_position_known is False


def test_feed_health_rejects_stale_or_late_batch_without_fill():
    event = _quote(1000)
    receipt = _simulate([event], feed_health=lambda _events: (False, ["STALE_QUOTE", "LATE_FEED"]))
    assert receipt["status"] == "NO_TRADE"
    assert receipt["reason"] == "LATE_FEED+STALE_QUOTE"
    assert receipt["fills"] == []
