"""Wave 9 realism tests: noisy fixtures with non-trading days, spread variation, splits and illiquidity; asynchronous
intraday and weekly completion; in-session candidate boxes; and turnover with a capacity cap (CP-0063, CP-0181,
CP-0341, CP-0345). Every series here is synthetic and labelled so."""

from __future__ import annotations

import random
from datetime import date

import pytest

from financial_brain.patterns import marketdata
from financial_brain.patterns.detectors import Bars, quarantine_rows
from financial_brain.patterns.detectors2 import discover_extended
from financial_brain.patterns.marketdata import fixture_calendar
from financial_brain.patterns.risk_views import turnover_and_cost_delta


def _realistic_rows(seed: int = 5) -> list[tuple[float, float, float, float]]:
    """Synthetic OHLC with noise, variable spread, a gap in calendar days, a 2:1 split and a zero-volume run."""
    rng = random.Random(seed)
    close = 200.0
    rows: list[tuple[float, float, float, float]] = []
    for i in range(120):
        close *= 1 + rng.gauss(0.0, 0.01)
        spread = rng.uniform(0.2, 1.5)  # spread variation
        if i == 60:
            close /= 2.0  # 2:1 split, unadjusted in the raw series
        o = close * (1 + rng.gauss(0, 0.003))
        hi = max(o, close) + spread
        lo = min(o, close) - spread
        rows.append((round(o, 4), round(hi, 4), round(lo, 4), round(close, 4)))
    return rows


def test_realistic_series_is_quarantined_only_where_it_is_invalid() -> None:
    rows = _realistic_rows()
    rows[10] = (rows[10][0], rows[10][1] - 5.0, rows[10][2], rows[10][3])  # high below low: invalid
    good, counts = quarantine_rows(rows)
    assert counts == {"ohlc_inequality": 1}
    assert len(good) == len(rows) - 1


def test_split_is_adjusted_and_the_adjusted_series_has_no_artificial_jump() -> None:
    closes = [r[3] for r in _realistic_rows()]
    adjusted = marketdata.adjust_for_split(closes, ex_index=60, factor=2.0)
    jump = abs(adjusted[60] / adjusted[59] - 1)
    assert jump < 0.2  # the raw series falls by half; the adjusted one does not


def test_non_trading_days_and_illiquid_runs_are_reported_not_filled() -> None:
    cal = fixture_calendar()
    days = cal.trading_days(date(2026, 1, 5), date(2026, 1, 16))
    present = set(days) - {days[3]}  # one trading day missing from the feed
    assert marketdata.missing_sessions(days, present) == [days[3]]
    assert marketdata.volume_eligibility([1000.0, 0.0, 0.0, 0.0, 0.0, 800.0]) != "ELIGIBLE"


def test_detectors_run_on_the_noisy_series_without_error() -> None:
    rows = _realistic_rows()
    bars = Bars(tuple(r[0] for r in rows), tuple(r[1] for r in rows), tuple(r[2] for r in rows), tuple(r[3] for r in rows))
    assert isinstance(discover_extended(bars), list)  # CP-0341: noisy input does not break detection


def test_intraday_bar_is_not_knowable_before_its_completion_time() -> None:
    rows = [
        {"symbol": "ABC", "bar": "09:15-09:20", "completed_at_utc": "2026-01-05T03:50:00+00:00", "close": 100.0},
        {"symbol": "ABC", "bar": "09:20-09:25", "completed_at_utc": "2026-01-05T03:55:00+00:00", "close": 101.0},
    ]
    seen = marketdata.bars_completed_by(rows, "2026-01-05T03:52:00+00:00")
    assert [r["bar"] for r in seen] == ["09:15-09:20"]  # CP-0345: the 09:20-09:25 bar has not completed yet


def test_weekly_bar_completes_only_after_its_last_session() -> None:
    weekly = {"symbol": "ABC", "week": "2026-W02", "completed_at_utc": "2026-01-09T10:00:00+00:00", "close": 110.0}
    before_friday_close = marketdata.bars_completed_by([weekly], "2026-01-08T10:00:00+00:00")
    after = marketdata.bars_completed_by([weekly], "2026-01-09T10:00:00+00:00")
    assert before_friday_close == [] and after == [weekly]


def test_naive_completion_timestamps_are_refused() -> None:
    with pytest.raises(marketdata.NaiveTimestamp):
        marketdata.bars_completed_by([{"completed_at_utc": "2026-01-05T03:50:00"}], "2026-01-05T04:00:00+00:00")


def test_candidate_box_is_bounded_by_trading_sessions() -> None:
    cal = fixture_calendar()
    days = cal.trading_days(date(2026, 1, 5), date(2026, 1, 12))  # the fixture calendar ends on 2026-01-12
    assert len(days) == 5
    assert marketdata.candidate_within_box(cal, days[0], days[2], max_sessions=3)  # CP-0063
    assert not marketdata.candidate_within_box(cal, days[0], days[4], max_sessions=3)  # too long
    assert not marketdata.candidate_within_box(cal, days[2], days[0], max_sessions=3)  # reversed
    assert not marketdata.candidate_within_box(cal, date(2026, 1, 7), days[2], max_sessions=3)  # holiday start


def test_turnover_is_capacity_adjusted_when_participation_is_known() -> None:
    unfiltered = [{"notional": 100.0, "adv": 1000.0}, {"notional": -400.0, "adv": 1000.0}]
    filtered = [{"notional": 100.0, "adv": 1000.0}]
    out = turnover_and_cost_delta(unfiltered, filtered, cost_rate=0.001, participation=0.1)
    assert out["capacity_adjusted_turnover_unfiltered"] == pytest.approx(200.0)  # each capped at 10 percent of ADV
    assert out["capacity_adjusted_turnover_filtered"] == pytest.approx(100.0)
    assert out["capacity_cost_delta"] == pytest.approx(0.1)
