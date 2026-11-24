"""FE-0058 passive queue model never fills on candle touches alone."""
from decimal import Decimal

import pytest

from financial_brain.forward_edge.passive_queue import PassiveQueueError, simulate_passive_fill


def test_candle_touch_without_trade_evidence_is_a_missed_fill() -> None:
    result = simulate_passive_fill(side="BUY", limit_price=Decimal("100"), quantity=10,
                                  best_bid=Decimal("100"), best_ask=Decimal("101"),
                                  traded_at_limit=0, candle_touched_limit=True)
    assert result.status == "MISSED" and result.filled_quantity == 0
    assert result.reason == "CANDLE_TOUCH_WITHOUT_TRADE_EVIDENCE"
    assert result.queue_position_known is False


def test_unknown_queue_position_is_disclosed_and_conservatively_missed() -> None:
    result = simulate_passive_fill(side="SELL", limit_price=Decimal("101"), quantity=5,
                                  best_bid=Decimal("100"), best_ask=Decimal("101"),
                                  traded_at_limit=20)
    assert result.status == "MISSED" and result.queue_position_known is False
    assert result.reason == "UNKNOWN_QUEUE_POSITION_CONSERVATIVE_NO_FILL"


@pytest.mark.parametrize("volume,expected", [(8, 0), (12, 2), (20, 5)])
def test_known_queue_position_produces_missed_partial_or_full_fill(volume, expected) -> None:
    result = simulate_passive_fill(side="BUY", limit_price=Decimal("100"), quantity=5,
                                  best_bid=Decimal("100"), best_ask=Decimal("101"),
                                  traded_at_limit=volume, queue_ahead=10, queue_position_known=True)
    assert result.filled_quantity == expected
    assert result.status == ("MISSED" if expected == 0 else "FILLED" if expected == 5 else "PARTIAL")


def test_invalid_queue_claim_or_book_is_rejected_or_no_fill() -> None:
    with pytest.raises(PassiveQueueError, match="requires"):
        simulate_passive_fill(side="BUY", limit_price=Decimal("100"), quantity=1,
                              best_bid=Decimal("100"), best_ask=Decimal("101"), traded_at_limit=2,
                              queue_position_known=True)
    no_book = simulate_passive_fill(side="BUY", limit_price=Decimal("100"), quantity=1,
                                    best_bid=None, best_ask=Decimal("101"), traded_at_limit=2)
    assert no_book.status == "MISSED" and no_book.reason == "INVALID_OR_MISSING_BOOK"
