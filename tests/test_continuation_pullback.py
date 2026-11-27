import pytest

from financial_brain.strategies.continuation_pullback import evaluate_continuation_pullback
from financial_brain.strategies.four_node.indicators import Cluster20
from financial_brain.strategies.four_node.nodes import Node


def test_bullish_higher_low_pullback_signals_after_close_for_next_bar():
    n1, n2, n3 = (Node(-1, 100, 2, 4), Node(1, 110, 6, 8), Node(-1, 104, 10, 12))
    signal = evaluate_continuation_pullback(
        n1, n2, n3, Cluster20(103, 105, 107), bar=13, c20_at=13,
        candle_low=104, candle_high=108, close=106, previous_close=105)
    assert signal is not None
    assert (signal.side, signal.signal_bar, signal.actionable_bar, signal.stop_reference) == (
        "LONG", 13, 14, 104)


def test_bearish_lower_high_is_symmetric():
    n1, n2, n3 = (Node(1, 110, 2, 4), Node(-1, 100, 6, 8), Node(1, 106, 10, 12))
    signal = evaluate_continuation_pullback(
        n1, n2, n3, Cluster20(103, 105, 107), bar=13, c20_at=13,
        candle_low=102, candle_high=106, close=104, previous_close=105)
    assert signal is not None and signal.side == "SHORT"


def test_unconfirmed_pivots_or_failed_band_reaction_cannot_signal():
    n1, n2, n3 = (Node(-1, 100, 2, 4), Node(1, 110, 6, 8), Node(-1, 104, 10, 13))
    args = dict(c20=Cluster20(103, 105, 107), bar=13, c20_at=13,
                candle_low=104, candle_high=108, close=106, previous_close=105)
    assert evaluate_continuation_pullback(n1, n2, n3, **args) is None
    confirmed = Node(-1, 104, 10, 12)
    assert evaluate_continuation_pullback(n1, n2, confirmed,
        Cluster20(103, 105, 107), bar=13, c20_at=13, candle_low=104,
        candle_high=108, close=104.5, previous_close=105) is None


def test_rejects_invalid_band_or_future_indicator_snapshot():
    nodes = (Node(-1, 100, 2, 4), Node(1, 110, 6, 8), Node(-1, 104, 10, 12))
    with pytest.raises(ValueError, match="available"):
        evaluate_continuation_pullback(*nodes, Cluster20(103, 105, 107), bar=13,
            c20_at=14, candle_low=104, candle_high=108, close=106, previous_close=105)
    with pytest.raises(ValueError, match="ordering"):
        evaluate_continuation_pullback(*nodes, Cluster20(107, 105, 103), bar=13,
            c20_at=13, candle_low=104, candle_high=108, close=106, previous_close=105)
