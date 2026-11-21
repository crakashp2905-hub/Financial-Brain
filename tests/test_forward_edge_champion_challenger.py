from datetime import date, timedelta

import pytest

from financial_brain.forward_edge.champion_challenger import (
    ChampionComparisonError,
    ComparisonSession,
    CostedPnl,
    compare_champion_to_frozen_family,
)


def _pnl(gross, *, capital=100_000, costs=10, source="a", schedule="b"):
    return CostedPnl(capital, gross, 0, costs, 0, 0, 0, 0, source * 64, schedule * 64)


def _rows(count=60, *, mismatch_capital=False, strong=True):
    result = []
    start = date(2026, 1, 1)
    for index in range(count):
        champion = _pnl(100, source="c")
        challengers = {
            "challenger-positive": _pnl(500 if strong else 100, source="d"),
            "challenger-weak": _pnl(110 if strong else 100, source="e"),
        }
        if mismatch_capital and index == 0:
            challengers["challenger-weak"] = _pnl(110, capital=90_000, source="e")
        result.append(ComparisonSession(start + timedelta(days=index), champion, challengers))
    return tuple(result)


def _compare(rows, **kwargs):
    return compare_champion_to_frozen_family(
        champion_id="champion-v3", challenger_ids=("challenger-positive", "challenger-weak"),
        expected_sessions=tuple(row.session for row in rows), rows=rows,
        expected_block_length=3, resamples=500, seed=5, minimum_sessions=60, **kwargs)


def test_paired_equal_capital_after_cost_family_test_detects_frozen_challenger():
    report = _compare(_rows())
    assert report.aligned_capital
    assert report.minimum_sessions_met
    assert report.observed_best_net_delta > 0
    assert report.familywise_p_value <= 0.05
    assert report.status == "FAMILYWISE_POSITIVE_NET_SIGNAL"
    assert not report.promotion_allowed
    assert len(report.report_hash) == 64


def test_no_net_improvement_or_insufficient_sessions_never_promotes():
    no_improvement = _compare(_rows(strong=False))
    assert no_improvement.observed_best_net_delta == 0
    assert no_improvement.status == "NO_FAMILYWISE_CHALLENGER_IMPROVEMENT"
    short_rows = _rows(20)
    too_short = _compare(short_rows)
    assert not too_short.minimum_sessions_met
    assert too_short.status == "INSUFFICIENT_SESSIONS"
    assert not too_short.promotion_allowed


def test_missing_sessions_or_unequal_capital_are_rejected():
    rows = _rows()
    with pytest.raises(ChampionComparisonError, match="every expected session"):
        compare_champion_to_frozen_family(
            champion_id="champion-v3", challenger_ids=("challenger-positive", "challenger-weak"),
            expected_sessions=tuple(row.session for row in rows), rows=rows[:-1],
            expected_block_length=3, resamples=200, seed=1, minimum_sessions=2)
    with pytest.raises(ChampionComparisonError, match="equal capital"):
        _compare(_rows(mismatch_capital=True))


def test_every_session_requires_same_frozen_candidate_family():
    rows = list(_rows())
    rows[0] = ComparisonSession(rows[0].session, rows[0].champion,
                                {"challenger-positive": _pnl(500)})
    with pytest.raises(ChampionComparisonError, match="complete frozen challenger family"):
        _compare(tuple(rows))


def test_costed_performance_uses_all_fees_and_requires_source_hashes():
    assert _pnl(100, costs=150).net_pnl == -50
    assert _pnl(100, costs=150).net_return < 0
    with pytest.raises(ChampionComparisonError, match="cost schedule source"):
        CostedPnl(100, 10, 0, 1, 0, 0, 0, 0, "a" * 64, "bad")
