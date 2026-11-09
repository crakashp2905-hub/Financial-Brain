"""Causal structure, deduplication and paper-execution accounting tests (ALG-020, ALG-023..032, ALG-041, ALG-084..094)."""

from __future__ import annotations

import random
from datetime import date

import pytest

from financial_brain.patterns.detectors import Bars, _const, _resolve
from financial_brain.patterns.pivots import causal_pivots
from financial_brain.strategies.execution_rules import (
    AcceptedOrder,
    FillRecord,
    Intent,
    NoFillRecord,
    Outcome,
    Signal,
    coverage_score,
    monte_carlo_drill,
    must_flat,
    next_open_fill_index,
    participation_fill,
    realised_pnl,
    resolve_intrabar,
    round_to_tick,
    stop_fill_price,
    usable_for_paper,
    viewer_summary,
)
from financial_brain.strategies.structure import (
    dedupe_events,
    higher_timeframe_known,
    pivot_marks,
    significant_swing,
    structure_labels,
    structure_state,
    trend_state,
    weekly_completed,
)


def _walk(seed: int, n: int = 150) -> tuple[list[float], list[float]]:
    rng = random.Random(seed)
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1 + rng.gauss(0, 0.01)))
    return [c + 0.3 for c in closes], [c - 0.3 for c in closes]


def test_pivot_is_marked_at_its_bar_and_recognised_later() -> None:  # ALG-031
    highs, lows = _walk(3)
    marks = pivot_marks(highs, lows)
    assert marks and all(m.recognised_at > m.index for m in marks)


def test_structure_labels_are_prefix_invariant() -> None:  # ALG-025
    highs, lows = _walk(4)
    pivots = causal_pivots(highs, lows, left=2, right=2)
    full = structure_labels(pivots)
    for t in range(20, len(highs), 9):
        prefix = structure_labels(causal_pivots(highs[: t + 1], lows[: t + 1], left=2, right=2))
        expected = [lab for lab in full if lab.known_at <= t]
        assert [(x.index, x.label) for x in prefix] == [(x.index, x.label) for x in expected]


def test_trend_state_reads_the_latest_labels() -> None:  # ALG-026
    assert trend_state(structure_labels_from(["HH", "HL"])) == "UPTREND"
    assert trend_state(structure_labels_from(["LH", "LL"])) == "DOWNTREND"
    assert trend_state([]) == "RANGE"


def structure_labels_from(labels: list[str]) -> list:
    from financial_brain.strategies.structure import StructureLabel

    return [StructureLabel(i, lab, i) for i, lab in enumerate(labels)]


def test_higher_timeframe_bar_must_close_before_the_signal() -> None:  # ALG-020
    assert higher_timeframe_known(htf_close_index=5, signal_index=6) is True
    assert higher_timeframe_known(htf_close_index=6, signal_index=6) is False


def test_weekly_bars_never_include_a_partial_week() -> None:  # ALG-027
    daily = [(date(2026, 1, d), 100.0 + d) for d in range(5, 12)]  # Mon 5 Jan to Sun 11 Jan 2026
    assert weekly_completed(daily, as_of=date(2026, 1, 8)) == []  # Friday not yet reached
    assert weekly_completed(daily, as_of=date(2026, 1, 9))[0][0] == "2026-W02"  # Friday closes the week


def test_dedupe_is_idempotent_across_replays() -> None:  # ALG-029, ALG-041
    e = {"lineage": "INE1", "timeframe": "1D", "pivot_indices": [3, 9]}
    replayed = [e, dict(e), dict(e, timeframe="1D")]
    assert len(dedupe_events(replayed)) == 1
    assert len(dedupe_events([e, {**e, "pivot_indices": [3, 10]}])) == 2


def test_swing_significance_is_scale_free() -> None:  # ALG-024
    assert significant_swing(100.0, 103.0, atr=2.0) is True
    assert significant_swing(100.0, 103.0, atr=2.0) == significant_swing(1000.0, 1030.0, atr=20.0)


def test_structure_states_for_flat_ranging_gapping_sequences() -> None:  # ALG-032
    assert structure_state([100.0, 100.0, 100.0]) == "FLAT"
    assert structure_state([100.0, 100.2, 99.9, 100.1]) == "RANGING"
    assert structure_state([100.0, 100.0, 110.0]) == "GAPPING"


def test_wick_only_breach_does_not_confirm() -> None:  # ALG-039
    bars = Bars.from_closes([100.0, 100.0, 100.0], spread=0.0)
    wick = Bars((100.0, 100.0, 100.0), (100.0, 102.0, 100.0), (100.0, 100.0, 100.0), (100.0, 100.0, 100.0))
    assert _resolve(wick, 0, up=_const(101.0)) == {}  # the high pierced, the close did not
    assert _resolve(bars, 0, up=_const(99.5)) == {"confirmed_index": 1, "confirmed_direction": "bullish"}


def test_realised_pnl_requires_a_fill() -> None:  # ALG-084
    with pytest.raises(NoFillRecord):
        realised_pnl(Outcome(fill=None, realised=10.0))
    intent = Intent(Signal(4, "LONG"), 10)
    fill = FillRecord(AcceptedOrder(intent, 4), fill_index=5, price=101.0, quantity=10)
    assert realised_pnl(Outcome(fill=fill, realised=12.0)) == 12.0


def test_signal_fills_on_the_next_bar_not_before() -> None:  # ALG-085
    assert next_open_fill_index(7) == 8


def test_ambiguous_bar_takes_the_adverse_branch() -> None:  # ALG-086
    assert resolve_intrabar(bar_high=105.0, bar_low=95.0, stop=97.0, target=103.0, side="LONG") == "STOP"
    assert resolve_intrabar(bar_high=104.0, bar_low=99.0, stop=97.0, target=103.0, side="LONG") == "TARGET"


def test_gap_through_stop_fills_at_the_adverse_open() -> None:  # ALG-087
    assert stop_fill_price(open_=94.0, stop=97.0, side="LONG") == 94.0
    assert stop_fill_price(open_=106.0, stop=97.0, side="SHORT") == 106.0
    assert round_to_tick(100.07, 0.05, direction="down") == 100.05


def test_participation_cap_reports_unfilled_quantity() -> None:  # ALG-088
    filled, unfilled = participation_fill(quantity=500, volume=2000, cap=0.1)
    assert (filled, unfilled) == (200, 300)
    assert filled + unfilled == 500  # equity reconciles


def test_intraday_and_futures_are_flat_on_time() -> None:  # ALG-089
    assert must_flat(now_hhmm="15:15", flat_at="15:15", product="CASH_EQUITY_INTRADAY", expiry=None, today=date(2026, 1, 5))
    assert must_flat(now_hhmm="10:00", flat_at="15:15", product="STOCK_FUTURES", expiry=date(2026, 1, 5), today=date(2026, 1, 5))
    with pytest.raises(ValueError):
        must_flat(now_hhmm="10:00", flat_at="15:15", product="STOCK_FUTURES", expiry=None, today=date(2026, 1, 5))


def test_viewer_summary_keeps_figures_separate() -> None:  # ALG-090
    out = viewer_summary(open_gross_mtm=50.0, closed_net=-20.0, modelled_closeout_cost=3.0, capital_at_risk=10_000.0)
    assert set(out) == {"open_gross_mtm", "closed_net", "modelled_closeout_cost", "capital_at_risk"}
    assert "total" not in out


def test_coverage_score_reports_the_denominator() -> None:  # ALG-092
    out = coverage_score(observed=2, replay=1, fixed=1)
    assert out["denominator"] == 4 and out["observed"] == 0.5


def test_unusable_strategy_is_marked_unavailable() -> None:  # ALG-093
    assert usable_for_paper({"instrument": "X", "cost_model_version": "v1", "spec_sha256": "a"}) is True
    assert usable_for_paper({"instrument": "X", "cost_model_version": "", "spec_sha256": "a"}) is False


def test_monte_carlo_drill_is_repeatable_under_a_fixed_seed() -> None:  # ALG-094
    assert monte_carlo_drill(7) == monte_carlo_drill(7)
    assert monte_carlo_drill(7) != monte_carlo_drill(8)
