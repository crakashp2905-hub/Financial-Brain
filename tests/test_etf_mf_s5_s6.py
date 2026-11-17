from __future__ import annotations

import pytest

from financial_brain.research.mf_metrics import (
    MfAction,
    direct_vs_regular,
    excess_vs_benchmark,
    nav_return,
    review,
    rolling_metrics,
)
from financial_brain.strategies.etf_rank import EtfObs, Output, Tier, rank_group, tier


def etf(uid, closes, group="EQ", listed=True, mtv=1e8, spread=5.0):
    return EtfObs(uid, group, listed, tuple(closes), mtv, spread)


UP = etf("A", [100, 101, 103, 106, 110])
FLAT = etf("B", [100, 100, 100, 100, 101])
GOLD = etf("G", [100, 120, 130, 140, 150], group="GOLD")


def test_rank_within_group_only():
    r = rank_group([UP, FLAT, GOLD], "EQ", lookback=4, order_value=1e5, cost_bps=10,
                   min_edge_bps=50, confidence_ok=True)
    assert [u for u, _ in r.order] == ["A", "B"]
    assert r.output is Output.ETF_RANKED


def test_shadow_when_confidence_fails_even_with_ranking():
    r = rank_group([UP, FLAT], "EQ", lookback=4, order_value=1e5, cost_bps=10, min_edge_bps=50,
                   confidence_ok=False)
    assert r.order and r.output is Output.ETF_SHADOW_ONLY


def test_shadow_when_cost_eats_edge():
    r = rank_group([FLAT], "EQ", lookback=4, order_value=1e5, cost_bps=200, min_edge_bps=50,
                   confidence_ok=True)
    assert r.output is Output.ETF_SHADOW_ONLY


def test_illiquid_and_unlisted_excluded():
    thin = etf("T", [1, 2, 3, 4, 5], mtv=1e5)
    wide = etf("W", [1, 2, 3, 4, 5], spread=120)
    fake = etf("L", [1, 2, 3, 4, 5], listed=False)
    r = rank_group([thin, wide, fake], "EQ", lookback=4, order_value=1e5, cost_bps=0,
                   min_edge_bps=0, confidence_ok=True)
    assert not r.order and len(r.excluded) == 3
    assert tier(wide, 1) is Tier.UNTRADEABLE and tier(thin, 1e5) is Tier.THIN


def test_short_history_excluded_not_guessed():
    r = rank_group([etf("S", [100, 101])], "EQ", lookback=4, order_value=1, cost_bps=0,
                   min_edge_bps=0, confidence_ok=True)
    assert r.excluded and not r.order


def test_nav_return_does_not_deduct_ter_again():
    assert nav_return([10.0, 11.0]) == pytest.approx(0.1)


def test_direct_vs_regular_uses_own_navs():
    assert direct_vs_regular([10, 11.1], [10, 11.0]) == pytest.approx(0.01)


def test_excess_vs_tri():
    assert excess_vs_benchmark([10, 11], [100, 108]) == pytest.approx(0.02)


def test_rolling_needs_full_window():
    assert rolling_metrics([10.0] * 100, 1) is None
    navs = [10 * (1.0004 ** i) for i in range(251)]
    r = rolling_metrics(navs, 1)
    assert r is not None and r.max_drawdown == 0 and r.cagr == pytest.approx(1.0004 ** 250 - 1)


def test_review_respects_category_horizon():
    assert review("EQUITY", 30, -0.1, underperform=-0.03, outperform=0.02) is MfAction.NO_ACTION
    assert review("EQUITY", 400, -0.1, underperform=-0.03, outperform=0.02) is MfAction.SWITCH
    assert review("EQUITY", 400, None, underperform=-0.03, outperform=0.02) is MfAction.NO_ACTION
    assert review("EQUITY", 400, 0.0, underperform=-0.03, outperform=0.02) is MfAction.HOLD
    assert review("EQUITY", 400, 0.05, underperform=-0.03, outperform=0.02) is MfAction.BUY_MORE
    with pytest.raises(ValueError):
        review("SCALP", 1, 0.0, underperform=0, outperform=0)
