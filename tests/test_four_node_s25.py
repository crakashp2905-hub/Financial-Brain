from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from financial_brain.strategies.four_node.indicators import FIB_RATIOS, Cluster20, fib_levels, smma
from financial_brain.strategies.four_node.nodes import Node, build, pivots
from financial_brain.strategies.four_node.rules import (
    Funnel,
    InstrumentConfig,
    Trail,
    evaluate,
    significant_candle,
    size_by_risk,
    trace,
)
from financial_brain.strategies.four_node.spec import (
    AMBIGUITIES,
    PINE_SHA256,
    SOURCE_SHA256,
    india_v1,
    original_xau,
)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs/strategies/four_node/source"


# ---- 25A source and ambiguity gates

def test_source_archived_verbatim():
    assert hashlib.sha256((SRC / "original_rules_2025-08-22.txt").read_bytes()).hexdigest() == SOURCE_SHA256
    assert hashlib.sha256((SRC / "xauusd_4node_reference.pine").read_bytes()).hexdigest() == PINE_SHA256


def test_variants_independent_and_india_blocked_until_owner_choices():
    xau, ind = original_xau(), india_v1()
    assert not xau.executable() and xau.params is not ind.params
    assert not ind.executable() and set(ind.unresolved()) == {a.id for a in AMBIGUITIES}
    with pytest.raises(ValueError):
        ind.choose("BUY_N3_MID", "MADE_UP", owner="owner")
    with pytest.raises(ValueError):
        ind.choose("BUY_N3_MID", "MIRROR_GE_MID", owner="")
    for a in AMBIGUITIES:
        ind.choose(a.id, a.alternatives[1], owner="owner")
    assert ind.executable()


def test_blocked_variant_fails_closed():
    ind = india_v1()
    nodes = (Node(1, 110, 1, 3), Node(-1, 100, 4, 6), Node(1, 108, 7, 9))
    s = evaluate(ind, nodes, Cluster20(101, 105, 109), 10, 99, [], 1.0, 0.1)
    assert s.status == "BLOCKED" and all(f.startswith("unresolved:") for f in s.failed)


# ---- 25B indicators and nodes

def test_smma_matches_wilder_reference():
    xs = [1.0, 2, 3, 4, 5, 6]
    out = smma(xs, 3)
    assert out[:2] == [None, None] and out[2] == 2.0
    assert out[3] == pytest.approx((2.0 * 2 + 4) / 3)
    assert smma([1.0, None, 2, 3, 4], 2)[1:] == [None, None, 2.5, 3.25]


def test_fib_ratio_set_and_prior_day_levels():
    assert len(FIB_RATIOS) == 27
    lv = fib_levels(100, 200, "PREV_DAY_LOW_TO_HIGH")
    assert lv[0.0] == 100 and lv[100.0] == 200 and lv[-100.0] == 0
    with pytest.raises(ValueError):
        fib_levels(100, 200, "TODAY")


def test_pivot_confirmation_delay_and_no_repaint():
    highs = [1, 2, 5, 2, 1, 3, 1, 1]
    lows = [h - 0.5 for h in highs]
    ps = pivots(highs, lows, 2, 2)
    top = [p for p in ps if p.side == 1][0]
    assert top.pivot_at == 2 and top.confirmed_at == 4
    longer = pivots(highs + [9, 9, 9], lows + [8, 8, 8], 2, 2)
    assert top in longer      # future bars do not change an emitted node


def test_zigzag_alternates_and_observability():
    highs = [5, 6, 9, 6, 5, 4, 3, 4, 5, 8, 5, 4, 3]
    lows = [h - 1 for h in highs]
    z = build(highs, lows, 2, 2)
    sides = [n.side for n in z.nodes]
    assert all(a != b for a, b in zip(sides, sides[1:]))
    assert z.last_three(3) is None


# ---- 25C entry, stops, exits

def xau_exec():
    v = original_xau()
    v.executable_intent = False   # reference variant evaluated for research only
    return v


def sell_nodes():
    return (Node(1, 2010.0, 1, 3), Node(-1, 2008.0, 4, 6), Node(1, 2009.0, 7, 9))


def test_sell_setup_eligible_with_reason_codes_and_next_bar_entry():
    s = evaluate(xau_exec(), sell_nodes(), Cluster20(2008.5, 2009.2, 2009.8), 10, 2007.8, [], 0.1, 0.2)
    assert s.status == "ELIGIBLE" and s.side == "SELL" and s.entry_bar == 11
    assert s.stop == pytest.approx(2009.2) and s.target == pytest.approx(2005.8)
    assert "breakout_close" in s.passed


def test_d2_boundary_is_exclusive():
    s = evaluate(xau_exec(), sell_nodes(), Cluster20(2008.5, 2009.2, 2009.8), 10, 2007.0, [], 0.1, 0.2)
    assert "d2_max" in s.failed


def test_unobservable_node_rejected():
    nodes = (Node(1, 2010.0, 1, 3), Node(-1, 2008.0, 4, 6), Node(1, 2009.0, 7, 12))
    assert evaluate(xau_exec(), nodes, Cluster20(2008.5, 2009.2, 2009.8), 10, 2007.8, [], 0.1, 0.2).failed == ("node_not_observable",)


def test_barrier_too_close_rejects():
    s = evaluate(xau_exec(), sell_nodes(), Cluster20(2008.5, 2009.2, 2009.8), 10, 2007.8, [2007.0], 0.1, 0.2)
    assert "barrier_clearance" in s.failed


def test_trailing_ratchets_and_never_loosens():
    t = Trail("BUY", 100.0, 98.5, 0.1, 10, 5)
    assert t.on_price(100.9) == 98.5 and t.tp_active
    assert t.on_price(101.0) == pytest.approx(100.5) and not t.tp_active
    assert t.on_price(101.5) == pytest.approx(101.0)
    assert t.on_price(100.2) == pytest.approx(101.0)
    s = Trail("SELL", 100.0, 101.5, 0.1, 10, 5)
    assert s.on_price(99.0) == pytest.approx(99.5)


def test_significant_candle_unresolved_is_none():
    assert significant_candle(10, 12, 8, 10.5, None) is None
    assert significant_candle(10, 12, 8, 10.5, "BOTH_WICKS_GT_BODY") is True
    assert significant_candle(10, 10.2, 9.9, 11, "COMBINED_WICK_GT_BODY") is False


# ---- 25D Indian adaptation

def test_risk_sizing_rounds_to_lots_not_forex_rule():
    cfg = InstrumentConfig("NIFTY_OPT", 0.05, 1, 20.0, 0.5, 0.5)
    assert size_by_risk(3000, 100, 80, cfg, lot=75) == 75 * 1
    assert size_by_risk(1000, 100, 80, cfg, lot=75) == 0


# ---- 25E funnel and trace

def test_funnel_dedupes_and_reconciles():
    f = Funnel()
    for i in range(5):
        f.add("pivots", f"p{i}")
    f.add("setups", "p1"); f.add("setups", "p1"); f.add("fills", "p1")
    assert f.counts["setups"] == 1 and f.reconciles(["pivots", "setups", "fills"])


def test_trace_record():
    s = evaluate(xau_exec(), sell_nodes(), Cluster20(2008.5, 2009.2, 2009.8), 10, 2007.8, [], 0.1, 0.2)
    t = trace(s, sell_nodes(), Cluster20(2008.5, 2009.2, 2009.8), 10.0, 4.0)
    assert t["nodes"][2]["confirmed_at"] == 9 and t["status"] == "ELIGIBLE"
