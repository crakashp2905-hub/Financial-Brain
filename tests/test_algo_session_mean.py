"""NSE session breakouts and mean-reversion controls (ALG-054..063, ALG-065..073)."""

from __future__ import annotations

import math

import pytest

from financial_brain.strategies.mean_reversion import (
    VwapBar,
    after_cost_utility,
    family_overlap_note,
    next_size_after_loss,
    pair_is_tradable,
    pair_spread_z,
    regime_abstain,
    session_vwap,
    vwap_rejection,
)
from financial_brain.strategies.session_breakout import (
    failed_breakout_signal,
    fill_if_available,
    ist_clock,
    oco_intents,
    opening_range,
    placebo_signal_times,
    range_excluded,
    retest_signal,
    run_oco,
)


def _minute(hhmm: str, h: float, lo: float, c: float, vol: float = 1000.0) -> dict:
    return {"time": hhmm, "open": c, "high": h, "low": lo, "close": c, "volume": vol}


def _session() -> list[dict]:
    bars = []
    for m in range(9 * 60 + 15, 9 * 60 + 30):  # 09:15 to 09:29 form the 15-minute range
        hh, mm = divmod(m, 60)
        bars.append(_minute(f"{hh:02d}:{mm:02d}", 101.0, 99.0, 100.0))
    for m in range(9 * 60 + 30, 15 * 60 + 20):
        hh, mm = divmod(m, 60)
        bars.append(_minute(f"{hh:02d}:{mm:02d}", 100.5, 99.5, 100.0))
    return bars


def test_opening_range_freezes_only_after_its_final_minute() -> None:  # ALG-055
    bars = _session()
    assert opening_range(bars[:10], minutes=15) is None  # the range has not finished
    rng = opening_range(bars, minutes=15)
    assert rng is not None and (rng.high, rng.low) == (101.0, 99.0) and rng.frozen_at == "09:30"


def test_range_sizes_are_restricted_to_five_fifteen_thirty() -> None:  # ALG-055
    with pytest.raises(ValueError):
        opening_range(_session(), minutes=10)


def test_oco_breakout_cancels_the_opposite_side_and_flats_at_the_flat_time() -> None:  # ALG-056, ALG-057
    bars = _session()
    rng = opening_range(bars, minutes=15)
    assert rng is not None
    bars[20] = _minute("09:50", 102.0, 100.0, 101.5)  # buy stop at 101 hit
    events = run_oco(bars, rng)
    kinds = [e.kind for e in events]
    assert "ENTRY_LONG" in kinds and "CANCEL_SHORT_STOP" in kinds
    assert "FLAT_END_OF_SESSION" in kinds
    assert "FLAT_UNCLOSED_GUARD" not in kinds  # nothing carried overnight


def test_oco_intents_are_the_range_edges() -> None:
    rng = opening_range(_session(), minutes=15)
    assert rng is not None
    intent = oco_intents(rng)
    assert (intent.buy_stop, intent.sell_stop) == (101.0, 99.0)


def test_wide_range_and_opening_gap_abstain() -> None:  # ALG-058
    rng = opening_range(_session(), minutes=15)
    assert rng is not None
    assert range_excluded(rng, prev_close=100.0, max_width_pct=0.001) == "RANGE_TOO_WIDE"
    assert range_excluded(rng, prev_close=100.0, open_price=106.0) == "OPENING_GAP"
    assert range_excluded(rng, prev_close=100.0) is None


def test_retest_must_come_strictly_after_confirmation() -> None:  # ALG-059
    closes = [99.0, 101.5, 100.8, 101.4]
    highs = [c + 0.2 for c in closes]
    lows = [99.0, 101.0, 100.4, 100.9]
    assert retest_signal(closes, highs, lows, level=101.0) == 3
    assert retest_signal(closes[:2], highs[:2], lows[:2], level=101.0) is None


def test_failed_breakout_needs_penetration_then_reentry() -> None:  # ALG-060
    closes = [100.0, 99.5, 100.2]
    lows = [100.0, 98.5, 99.7]
    assert failed_breakout_signal(closes, lows, level=99.0) == 1  # penetration at bar 1, reentry on the same bar
    assert failed_breakout_signal([100.0, 98.5], [100.0, 98.5], level=99.0) is None  # no reentry


def test_placebo_uses_the_same_clock_without_geometry() -> None:  # ALG-061
    bars = _session()
    assert placebo_signal_times(bars, clock="09:30") == ["09:30"]


def test_halts_and_zero_volume_give_no_fill() -> None:  # ALG-062
    assert fill_if_available(None) is None
    assert fill_if_available(_minute("10:00", 101, 99, 100, vol=0.0)) is None
    assert fill_if_available(_minute("10:00", 101, 99, 100), halted=True) is None
    assert fill_if_available(_minute("10:00", 101, 99, 100)) == 100.0


def test_ist_clock_converts_utc_and_refuses_naive_times() -> None:  # ALG-063
    assert ist_clock("2026-01-05T03:45:00+00:00") == "09:15"
    with pytest.raises(ValueError):
        ist_clock("2026-01-05T03:45:00")


def test_vwap_resets_each_session() -> None:  # ALG-065
    bars = [
        VwapBar("D1", 101, 99, 100, 1000),
        VwapBar("D2", 201, 199, 200, 1000),
    ]
    vwap = session_vwap(bars)
    assert vwap[1] == pytest.approx(200.0)  # prior session volume does not leak into day two


def test_vwap_rejection_needs_closed_rejection_bars() -> None:  # ALG-066
    lows = [100.0, 98.0, 99.0]
    closes = [100.5, 99.0, 101.0]
    vwap = [100.0, 99.8, 100.0]
    assert vwap_rejection(lows, closes, vwap, 2) is True
    assert vwap_rejection(lows, closes, vwap, 1) is False


def test_regime_abstains_on_lagged_volatility_shock() -> None:  # ALG-068
    calm = [100.0 + 0.01 * i for i in range(120)]
    assert regime_abstain(calm, 100) is False
    shocked = calm[:100] + [100.0 * (1.2 if i % 2 else 0.8) for i in range(20)]
    assert regime_abstain(shocked, 110) is True
    assert regime_abstain(calm, 5) is True  # not enough history: abstain


def test_pair_hedge_is_fit_on_training_data_only() -> None:  # ALG-069
    train_b = [100 + i for i in range(30)]
    train_a = [2.0 * b + 1.0 + math.sin(i) for i, b in enumerate(train_b)]
    test_b = [200.0, 201.0, 202.0]
    test_a = [2.0 * b + 1.0 for b in test_b]
    z = pair_spread_z(train_a, train_b, test_a, test_b)
    assert len(z) == 3 and all(math.isfinite(v) for v in z)


def test_cash_equity_short_leg_pairs_are_rejected() -> None:  # ALG-070
    assert pair_is_tradable("CASH_EQUITY") is False
    assert pair_is_tradable("STOCK_FUTURES") is True


def test_size_never_grows_after_a_loss() -> None:  # ALG-071
    assert next_size_after_loss(10, -5.0) == 10


def test_k7_null_stays_visible_for_reversion() -> None:  # ALG-072
    assert any("K7" in n for n in family_overlap_note("mean_reversion"))


def test_after_cost_utility_shows_turnover_next_to_net() -> None:  # ALG-073
    out = after_cost_utility([0.01, 0.01], [1.0, 1.0], cost_bps=10)
    assert out["gross_total"] == pytest.approx(0.02)
    assert out["net_total"] == pytest.approx(0.02 - 2 * 0.001)
    assert out["turnover_total"] == 2.0
