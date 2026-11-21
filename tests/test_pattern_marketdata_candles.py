"""Market-data contracts and candle primitives (CP-0043..0055, CP-0108..0120)."""

from __future__ import annotations

import random
from datetime import date

import pandas as pd
import pytest

from financial_brain.features import library

from financial_brain.patterns.candles import (
    CANDLE_MAP,
    K7_NOTICE,
    DailyBar,
    causal_check,
    classify_gap_with_exclusions,
    gap_kind,
    is_inference_eligible,
    library_candle_events,
    mtf_known_at_monotonic,
    weekly_bars,
)
from financial_brain.patterns.marketdata import (
    ENTITLEMENTS,
    IdentityMap,
    NaiveTimestamp,
    SecurityIdentity,
    SegmentKind,
    SessionKind,
    adjust_for_split,
    fixture_calendar,
    freshness,
    missing_sessions,
    normalize_timestamp,
    open_external_dataset,
    shorting_allowed,
    snapshot,
    volume_eligibility,
)
from financial_brain.patterns.offline import OfflineViolation


# ---------------------------------------------------------------- stage 03
def test_naive_timestamp_refused_and_aware_normalised() -> None:
    with pytest.raises(NaiveTimestamp):
        normalize_timestamp("2026-01-05T09:15:00")
    ist, utc = normalize_timestamp("2026-01-05T09:15:00+05:30")
    assert ist.endswith("+05:30")
    assert utc == "2026-01-05T03:45:00+00:00"


def test_calendar_is_table_driven_not_weekday_arithmetic() -> None:
    cal = fixture_calendar()
    assert not cal.is_trading(date(2026, 1, 7))  # holiday on a weekday
    assert cal.info(date(2026, 1, 8)).kind is SessionKind.SHORTENED
    assert cal.info(date(2026, 1, 10)) is None  # not in the table: no assumed session
    assert cal.trading_days(date(2026, 1, 5), date(2026, 1, 9)) == [
        date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 8), date(2026, 1, 9)
    ]


def test_identity_withholds_ambiguous_aliases() -> None:
    im = IdentityMap()
    im.add("OLDNAME", SecurityIdentity("L1", "INE001"))
    im.add("SHARED", SecurityIdentity("L1", "INE001"))
    im.add("SHARED", SecurityIdentity("L2", "INE002"))
    assert im.resolve("OLDNAME") == SecurityIdentity("L1", "INE001")
    assert im.resolve("SHARED") is None  # two identities: withheld
    assert im.resolve("NOPE") is None


def test_split_adjustment_removes_fictional_drop() -> None:
    raw = [100.0, 101.0, 50.0, 51.0]  # 2:1 split on index 2
    adjusted = adjust_for_split(raw, ex_index=2, factor=2.0)
    assert adjusted == [50.0, 50.5, 50.0, 51.0]
    with pytest.raises(ValueError):
        adjust_for_split(raw, ex_index=0, factor=2.0)


def test_snapshot_is_deterministic_and_order_independent() -> None:
    rows = [("2026-01-06", 1, 2, 0.5, 1.5, 10.0), ("2026-01-05", 1, 2, 0.5, 1.2, 9.0)]
    a = snapshot(rows)
    b = snapshot(list(reversed(rows)))
    assert a == b
    assert a["row_count"] == 2 and a["min_session"] == "2026-01-05" and a["max_session"] == "2026-01-06"
    assert snapshot([])["row_count"] == 0


def test_volume_gating_and_shorting_rules() -> None:
    assert volume_eligibility([10, 12, 11]) == "OK"
    assert volume_eligibility([10, None, 11]) == "DATA_INSUFFICIENT"
    assert volume_eligibility([10, 0, 0, 0, 0, 9]) == "DATA_INSUFFICIENT"
    assert shorting_allowed(SegmentKind.CASH_EQUITY, has_fixture_support=True) is False
    assert shorting_allowed(SegmentKind.INDEX_FUTURE, has_fixture_support=True) is True
    assert shorting_allowed(SegmentKind.INDEX_FUTURE, has_fixture_support=False) is False


def test_missing_sessions_listed_not_filled() -> None:
    expected = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 8)]
    assert missing_sessions(expected, {date(2026, 1, 5), date(2026, 1, 8)}) == [date(2026, 1, 6)]


def test_freshness_abstains_when_stale() -> None:
    assert freshness(last_session=date(2026, 1, 5), as_of=date(2026, 1, 5), expected_sessions_after=0, max_lag=2).status == "FRESH"
    stale = freshness(last_session=date(2026, 1, 1), as_of=date(2026, 1, 9), expected_sessions_after=5, max_lag=2)
    assert stale.status == "STALE"
    assert freshness(last_session=date(2026, 1, 9), as_of=date(2026, 1, 5), expected_sessions_after=0, max_lag=2).status == "INVALID"


def test_external_datasets_blocked_offline_and_local_one_needs_no_network() -> None:
    with pytest.raises(OfflineViolation):
        open_external_dataset("intraday_quotes")
    open_external_dataset("local_eod_duckdb")  # installed and local: no error
    assert ENTITLEMENTS["local_eod_duckdb"]["network"] is False


# ---------------------------------------------------------------- stage 07
def _ohlcv(n: int = 120, seed: int = 4) -> pd.DataFrame:
    rng = random.Random(seed)
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(max(5.0, closes[-1] * (1 + rng.gauss(0, 0.012))))
    opens = [closes[0]] + closes[:-1]
    highs = [max(o, c) + abs(rng.gauss(0, 0.4)) for o, c in zip(opens, closes, strict=True)]
    lows = [min(o, c) - abs(rng.gauss(0, 0.4)) for o, c in zip(opens, closes, strict=True)]
    days = pd.bdate_range("2026-01-05", periods=n)
    return pd.DataFrame(
        {
            "d": days.strftime("%Y-%m-%d"),
            "o": opens,
            "h": highs,
            "l": lows,
            "c": closes,
            "v": [1000.0 + rng.random() * 100 for _ in range(n)],
        }
    )


def test_candle_inventory_covers_existing_library_columns() -> None:
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src" / "financial_brain" / "features" / "library.py").read_text(encoding="utf-8")
    for column in CANDLE_MAP:
        assert f'"{column}"' in source, column


def test_library_candle_events_are_timed_and_causal() -> None:
    frame = _ohlcv()
    events = library_candle_events(frame)
    assert events, "fixture should contain at least one candle shape"
    for event in events:
        assert event.date == frame["d"].iloc[event.index]
    assert causal_check(frame, cutoff=60)
    assert causal_check(frame, cutoff=90)


def test_hammer_only_after_a_decline() -> None:
    frame = _ohlcv(n=160, seed=9)
    feats = library.compute(frame)
    for i, value in enumerate(feats["c_hammer"].tolist()):
        if value == 1:
            assert frame["c"].iloc[i] < frame["c"].iloc[i - 5]


def test_gap_kind_separates_range_gap_from_body_gap() -> None:
    # range gap: today's low above yesterday's high
    assert gap_kind(10, 9, 9.2, 9.5, 12, 10.5, 10.6, 11.5) == "range_gap_up"
    # body gap only: bodies separated but the ranges still overlap
    assert gap_kind(10, 9, 9.2, 9.5, 10.4, 9.8, 9.9, 10.2) == "body_gap_up"
    assert gap_kind(10, 9, 9.2, 9.5, 9.9, 9.1, 9.3, 9.4) is None


def test_gaps_from_corporate_action_or_circuit_are_excluded() -> None:
    assert classify_gap_with_exclusions("range_gap_up", corporate_action=True, circuit_frozen=False) == "EXCLUDED_CORPORATE_ACTION"
    assert classify_gap_with_exclusions("range_gap_up", corporate_action=False, circuit_frozen=True) == "EXCLUDED_CIRCUIT_FREEZE"
    assert classify_gap_with_exclusions("range_gap_up", corporate_action=False, circuit_frozen=False) == "range_gap_up"
    assert classify_gap_with_exclusions(None, corporate_action=False, circuit_frozen=False) == "NO_GAP"


def test_pending_definitions_are_excluded_from_inference() -> None:
    assert is_inference_eligible("Engulfing") is True
    assert is_inference_eligible("Tasuki gaps") is False
    assert is_inference_eligible("separating lines") is False


def test_partial_week_is_never_final() -> None:
    days = [
        DailyBar(date(2026, 1, 5), 100, 102, 99, 101, 10),
        DailyBar(date(2026, 1, 6), 101, 103, 100, 102, 11),
        DailyBar(date(2026, 1, 12), 102, 104, 101, 103, 12),  # next ISO week begins
    ]
    weeks = weekly_bars(days, week_end_seen=None)
    assert [w["status"] for w in weeks] == ["FINAL", "PARTIAL"]
    assert weeks[0]["sessions"] == 2 and weeks[0]["high"] == 103 and weeks[0]["low"] == 99


def test_multi_timeframe_known_at_is_monotonic() -> None:
    assert mtf_known_at_monotonic([date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 6)])
    assert not mtf_known_at_monotonic([date(2026, 1, 6), date(2026, 1, 5)])


def test_k7_notice_is_present_and_not_softened() -> None:
    assert "failed its preregistered G3 gate" in K7_NOTICE
    assert "not evidence of a working pattern" in K7_NOTICE

