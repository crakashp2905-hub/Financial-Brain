from __future__ import annotations

import pytest

from financial_brain.strategies.four_node.indicators import Cluster20
from financial_brain.strategies.four_node.nodes import Node
from financial_brain.strategies.four_node.rules import (
    barriers_split,
    evaluate,
    nodes_expired,
    round_stop,
    trail_price,
)
from financial_brain.strategies.four_node.spec import AMBIGUITIES, india_v1, original_xau


def ref():
    v = original_xau()
    v.executable_intent = False
    return v


SELL = (Node(1, 2010.0, 1, 3), Node(-1, 2008.0, 4, 6), Node(1, 2009.0, 7, 9))


@pytest.mark.parametrize("low,ok", [(2008.0, True), (2007.99, False)])
def test_n2_vs_20c_low_is_inclusive(low, ok):
    s = evaluate(ref(), SELL, Cluster20(low, 2009.2, 2009.8), 10, 2007.8, [], 0.1, 0.2)
    assert ("n2_vs_20c_low" in s.passed) is ok


def buy_nodes():
    return (Node(-1, 1990.0, 1, 3), Node(1, 1992.0, 4, 6), Node(-1, 1991.0, 7, 9))


def test_buy_comparator_variants_differ_on_boundary():
    lit = india_v1()
    mir = india_v1()
    for a in AMBIGUITIES:
        lit.choose(a.id, a.alternatives[0], owner="owner")
        mir.choose(a.id, a.alternatives[1], owner="owner")
    c20 = Cluster20(1990.5, 1991.5, 1991.9)
    a = evaluate(lit, buy_nodes(), c20, 10, 1992.2, [], 1.0, 0.2)
    b = evaluate(mir, buy_nodes(), c20, 10, 1992.2, [], 1.0, 0.2)
    assert ("n2_vs_20c_high" in a.passed) != ("n2_vs_20c_high" in b.passed)


def test_round_stop_never_tightens():
    assert round_stop(2009.23, 0.05, "SELL") == pytest.approx(2009.25)
    assert round_stop(1990.27, 0.05, "BUY") == pytest.approx(1990.25)


def test_trail_uses_executable_side():
    assert trail_price("BUY", 99.9, 100.0) == 99.9 and trail_price("SELL", 99.9, 100.0) == 100.0
    with pytest.raises(ValueError):
        trail_price("BUY", 101, 100)


def test_barriers_ahead_and_behind():
    ahead, behind = barriers_split(100.0, "BUY", [95.0, 105.0, 110.0])
    assert ahead == [105.0, 110.0] and behind == [95.0]


def test_node_expiry():
    assert not nodes_expired(SELL, 20, 30) and nodes_expired(SELL, 40, 30)
