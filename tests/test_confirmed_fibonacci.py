import pytest

from financial_brain.strategies.confirmed_fibonacci import confirmed_fibonacci_levels
from financial_brain.strategies.four_node.nodes import Node


def test_fibonacci_projection_uses_only_confirmed_alternating_anchors():
    low = Node(-1, 100, pivot_at=10, confirmed_at=12)
    high = Node(1, 120, pivot_at=15, confirmed_at=18)
    levels = confirmed_fibonacci_levels(low, high, as_of_bar=18, ratios=(0, 0.5, 1, 1.618))
    assert levels == {0: 100, 0.5: 110, 1: 120, 1.618: 132.36}


def test_fibonacci_rejects_unconfirmed_future_and_nonalternating_anchors():
    first = Node(-1, 100, 2, 3)
    second = Node(1, 110, 5, 8)
    with pytest.raises(ValueError, match="not observable"):
        confirmed_fibonacci_levels(first, second, as_of_bar=7)
    with pytest.raises(ValueError, match="alternate"):
        confirmed_fibonacci_levels(first, Node(-1, 105, 5, 6), as_of_bar=7)
    with pytest.raises(ValueError, match="chronological"):
        confirmed_fibonacci_levels(second, first, as_of_bar=10)
