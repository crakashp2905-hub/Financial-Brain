from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from financial_brain.strategies.four_node.indicators import (
    FIB_RATIOS, Cluster20, close_smma_bands, cluster20, daily_fib_snapshot, fib_levels, smma,
)
from financial_brain.strategies.four_node.nodes import Node, Zigzag, build, pivots
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


def test_smma_session_reset_is_explicit_and_bands_have_golden_ohlc_values():
    assert smma([1.0, 2.0, 3.0, 4.0], 2,
                session_ids=["A", "A", "B", "B"], reset_policy="ON_SESSION_CHANGE") == [
                    None, 1.5, None, 3.5]
    with pytest.raises(ValueError, match="session_ids are required"):
        smma([1.0], 1, reset_policy="ON_SESSION_CHANGE")
    c20 = cluster20([float(i) for i in range(1, 21)],
                    [float(i + 1) for i in range(1, 21)],
                    [float(i + 2) for i in range(1, 21)])
    assert c20[:19] == [None] * 19
    assert c20[19] == Cluster20(10.5, 11.5, 12.5)


def test_close_smma_periods_align_and_match_independent_wilder_reference():
    values = [float(i) for i in range(1, 221)]
    bands = close_smma_bands(values)
    assert set(bands) == {8, 50, 100, 200}
    for n, actual in bands.items():
        assert len(actual) == len(values)
        assert actual[:n - 1] == [None] * (n - 1)
        expected = sum(values[:n]) / n
        assert actual[n - 1] == pytest.approx(expected)
        for value in values[n:]:
            expected = (expected * (n - 1) + value) / n
        assert actual[-1] == pytest.approx(expected)


def test_fib_ratio_set_and_prior_day_levels():
    assert len(FIB_RATIOS) == 27
    lv = fib_levels(100, 200, "PREV_DAY_LOW_TO_HIGH")
    assert lv[0.0] == 100 and lv[100.0] == 200 and lv[-100.0] == 0
    with pytest.raises(ValueError):
        fib_levels(100, 200, "TODAY")


def test_daily_fib_snapshot_binds_levels_to_completed_session_and_version():
    snapshot = daily_fib_snapshot(
        prev_day_low=100, prev_day_high=200, anchor="PREV_DAY_LOW_TO_HIGH",
        anchor_session_id="NSE:2026-11-24",
        anchor_close_at=datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc),
        evaluated_session_open_at=datetime(2026, 11, 25, 3, 45, tzinfo=timezone.utc),
    )
    assert snapshot.version == "four-node-daily-fib-v1"
    assert snapshot.anchor_session_id == "NSE:2026-11-24"
    assert snapshot.anchor_close_at < snapshot.evaluated_session_open_at
    assert snapshot.levels[100.0] == 200
    with pytest.raises(ValueError, match="must be completed"):
        daily_fib_snapshot(
            prev_day_low=100, prev_day_high=200, anchor="PREV_DAY_LOW_TO_HIGH",
            anchor_session_id="current-session",
            anchor_close_at=datetime(2026, 11, 25, 4, 0, tzinfo=timezone.utc),
            evaluated_session_open_at=datetime(2026, 11, 25, 3, 45, tzinfo=timezone.utc),
        )


def test_pivot_confirmation_delay_and_no_repaint():
    highs = [1, 2, 5, 2, 1, 3, 1, 1]
    lows = [h - 0.5 for h in highs]
    ps = pivots(highs, lows, 2, 2)
    top = [p for p in ps if p.side == 1][0]
    assert top.pivot_at == 2 and top.confirmed_at == 4
    longer = pivots(highs + [9, 9, 9], lows + [8, 8, 8], 2, 2)
    assert top in longer      # future bars do not change an emitted node


def test_pivot_tie_policy_minimum_swing_and_node_lineage():
    highs = [1, 4, 4, 2, 1, 3, 1]
    lows = [h - 1 for h in highs]
    strict = pivots(highs, lows, 1, 1, bar_ids=[f"b{i}" for i in range(len(highs))])
    leftmost = pivots(highs, lows, 1, 1, tie_policy="LEFTMOST")
    rightmost = pivots(highs, lows, 1, 1, tie_policy="RIGHTMOST")
    assert not any(n.side == 1 and n.pivot_at in (1, 2) for n in strict)
    assert any(n.side == 1 and n.pivot_at == 1 for n in leftmost)
    assert any(n.side == 1 and n.pivot_at == 2 for n in rightmost)
    pivot = next(n for n in strict if n.side == 1 and n.pivot_at == 5)
    assert pivot.observable_at == pivot.confirmed_at == 6
    assert pivot.source_bar_ids == ("b4", "b5", "b6")
    assert pivot.node_id
    with pytest.raises(ValueError, match="before confirmation"):
        Node(1, 5, 2, 4, observable_at=3)
    delayed = Node(1, 9, 2, 4, observable_at=5)
    eligibility = Zigzag(nodes=[Node(1, 10, 0, 2), Node(-1, 8, 3, 4), delayed])
    assert eligibility.last_three(4) is None
    assert eligibility.last_three(5) == (eligibility.nodes[0], eligibility.nodes[1], delayed)

    z = build([1, 5, 2, 4, 1, 6, 2, 4, 1],
              [0, 4, 1, 3, 0, 5, 1, 3, 0], left=1, right=1, min_swing=3)
    assert all(a.side != b.side for a, b in zip(z.nodes, z.nodes[1:]))
    assert all(abs(a.price - b.price) >= 3 for a, b in zip(z.nodes, z.nodes[1:]))
    assert all(n.parent_chain for n in z.nodes[1:])


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
    n1, n2, n3 = sell_nodes()
    inclusive = evaluate(xau_exec(), (n1, n2, n3), Cluster20(2008.5, 2009.2, 2009.8),
                         10, 2007.0, [], 0.1, 0.2, d2_inclusive=True)
    assert "d2_max" in inclusive.passed


def test_d1_and_d2_boundary_policies_are_independent_and_configurable():
    nodes = (Node(1, 2030.0, 1, 3), Node(-1, 2008.0, 4, 6), Node(1, 2023.0, 7, 9))
    # D1 is exactly 15 units and D2 exactly 10 units; widen only the independent stop gate.
    variant = xau_exec()
    variant.params["max_sl"] = 100
    result = evaluate(variant, nodes, Cluster20(2008.0, 2020.0, 2030.0), 10,
                      1998.0, [], 1.0, 0.2, d1_inclusive=False, d2_inclusive=True)
    assert "d1_max" in result.failed
    assert "d2_max" in result.passed


def test_unobservable_node_rejected():
    nodes = (Node(1, 2010.0, 1, 3), Node(-1, 2008.0, 4, 6), Node(1, 2009.0, 7, 12))
    assert evaluate(xau_exec(), nodes, Cluster20(2008.5, 2009.2, 2009.8), 10, 2007.8, [], 0.1, 0.2).failed == ("node_not_observable_before_breakout_bar",)


def test_barrier_too_close_rejects():
    s = evaluate(xau_exec(), sell_nodes(), Cluster20(2008.5, 2009.2, 2009.8), 10, 2007.8, [2007.0], 0.1, 0.2)
    assert "barrier_clearance" in s.failed
    behind = evaluate(xau_exec(), sell_nodes(), Cluster20(2008.5, 2009.2, 2009.8),
                      10, 2007.8, [2008.0], 0.1, 0.2)
    assert "barrier_clearance" in behind.passed


def test_initial_stops_are_tick_rounded_away_from_entry_and_side_checked():
    sell = evaluate(xau_exec(), sell_nodes(), Cluster20(2008.5, 2009.2, 2009.8),
                    10, 2007.8, [], 0.2, 0.3, tick=0.25)
    assert sell.stop == pytest.approx(2009.5)
    assert "stop_side_valid" in sell.passed

    buy_nodes = (Node(-1, 1990.0, 1, 3), Node(1, 1992.0, 4, 6), Node(-1, 1991.0, 7, 9))
    buy = evaluate(xau_exec(), buy_nodes, Cluster20(1990.0, 1991.0, 1992.0),
                   10, 1992.1, [], 0.2, 0.3, tick=0.25)
    assert buy.stop == pytest.approx(1990.5)
    assert "stop_side_valid" in buy.passed

    invalid = evaluate(xau_exec(), sell_nodes(), Cluster20(2008.5, 2009.2, 2009.8),
                       10, 2010.0, [], 0.2, 0.3)
    assert "stop_side_valid" in invalid.failed


def test_trailing_ratchets_and_never_loosens():
    t = Trail("BUY", 100.0, 98.5, 0.1, 10, 5)
    assert t.on_price(100.9) == 98.5 and t.tp_active
    assert t.on_price(101.0) == pytest.approx(100.5) and not t.tp_active
    assert t.on_price(101.5) == pytest.approx(101.0)
    assert t.on_price(100.2) == pytest.approx(101.0)
    s = Trail("SELL", 100.0, 101.5, 0.1, 10, 5)
    assert s.on_price(99.0) == pytest.approx(99.5)


def test_quote_driven_trailing_uses_executable_side_and_resists_spread_false_trigger():
    long = Trail("BUY", 100.0, 95.0, 1.0, 10.0, 5.0)
    assert long.on_quote(109.9, 110.4) == 95.0  # ask crossed, long exit bid did not
    assert long.tp_active
    assert long.on_quote(110.0, 110.5) == 105.0
    assert not long.tp_active

    short = Trail("SELL", 100.0, 105.0, 1.0, 10.0, 5.0)
    assert short.on_quote(89.5, 90.0) == 95.0  # short cover price is the ask
    with pytest.raises(ValueError, match="crossed quote"):
        short.on_quote(100.1, 100.0)


def test_significant_candle_unresolved_is_none():
    assert significant_candle(10, 12, 8, 10.5, None) is None
    assert significant_candle(10, 12, 8, 10.5, "BOTH_WICKS_GT_BODY") is True
    assert significant_candle(10, 10.2, 9.9, 11, "COMBINED_WICK_GT_BODY") is False


# ---- 25D Indian adaptation

def test_risk_sizing_rounds_to_lots_not_forex_rule():
    cfg = InstrumentConfig("NIFTY_OPT", 0.05, 1, 20.0, 0.5, 0.5,
                           trading_hours=("09:15", "15:30"), corporate_action_factor=1.0)
    assert size_by_risk(3000, 100, 80, cfg, lot=75) == 75 * 1
    assert size_by_risk(1000, 100, 80, cfg, lot=75) == 0
    assert size_by_risk(3000, 100, 80, cfg, lot=1) * 21 <= 3000
    with pytest.raises(ValueError, match="lot must"):
        size_by_risk(3000, 100, 80, cfg, lot=0)
    with pytest.raises(ValueError, match="risk budget"):
        size_by_risk(-1, 100, 80, cfg)
    with pytest.raises(ValueError, match="trading hours"):
        InstrumentConfig("BAD", 0.05, 1, 20.0, 0.0, 0.0, trading_hours=("15:30", "09:15"))


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
