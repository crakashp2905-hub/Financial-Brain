"""D2 point-in-time validators: NAV availability, NAV row flags, ETF quote gating, iNAV comparability."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.instruments.pit import (
    Inav,
    NavFlag,
    PitError,
    PriceStatus,
    Quote,
    fill_reference,
    inav_premium,
    nav_row_flags,
    nav_series_moves,
    nav_usable,
    quote_at,
)

UTC = timezone.utc


def ts(h: int, m: int = 0, d: int = 17) -> datetime:
    return datetime(2026, 11, d, h, m, tzinfo=UTC)


def at(h: int, m: int = 0, s: int = 0) -> datetime:
    return datetime(2026, 11, 17, h, m, s, tzinfo=UTC)


def q(bid="99.50", ask="100.00", ex=None, rc=None, bq=10, aq=10) -> Quote:
    return Quote(Decimal(bid), Decimal(ask), ex or ts(10), rc or ts(10, 0, 17), bq, aq)


# ---------- NAV availability


def test_nav_is_not_usable_before_it_is_published():
    assert nav_usable(ts(21), ts(18)) is False


def test_nav_published_exactly_at_decision_is_usable():
    assert nav_usable(ts(21), ts(21)) is True


def test_same_day_nav_published_after_cutoff_is_not_usable_for_a_morning_decision():
    assert nav_usable(ts(22), ts(9)) is False


def test_naive_timestamp_is_refused():
    with pytest.raises(PitError):
        nav_usable(datetime(2026, 11, 17, 21), ts(18))


# ---------- NAV row flags


def test_fresh_nav_has_no_flags():
    assert nav_row_flags(date(2026, 11, 16), date(2026, 11, 17), max_lag_days=3) == []


def test_nav_dated_after_its_file_is_refused_as_future_dated():
    assert nav_row_flags(date(2026, 11, 18), date(2026, 11, 17), max_lag_days=3) == [NavFlag.FUTURE_DATED]


def test_nav_behind_the_file_beyond_the_lag_is_flagged_stale():
    assert nav_row_flags(date(2018, 3, 1), date(2026, 11, 17), max_lag_days=3) == [NavFlag.STALE]


def test_negative_lag_threshold_is_refused():
    with pytest.raises(PitError):
        nav_row_flags(date(2026, 11, 16), date(2026, 11, 17), max_lag_days=-1)


def test_large_nav_move_is_flagged_and_keeps_both_dates():
    series = [(date(2026, 11, 13), "10.00"), (date(2026, 11, 16), "10.10"), (date(2026, 11, 17), "5.00")]
    moves = nav_series_moves(series, max_move="0.2")
    assert len(moves) == 1
    assert moves[0].on == date(2026, 11, 17)
    assert moves[0].previous == date(2026, 11, 16)
    assert moves[0].flag is NavFlag.LARGE_MOVE


def test_nav_series_must_be_strictly_increasing():
    with pytest.raises(PitError):
        nav_series_moves([(date(2026, 11, 17), "10"), (date(2026, 11, 16), "10")], max_move="0.2")


def test_non_positive_nav_is_refused():
    with pytest.raises(PitError):
        nav_series_moves([(date(2026, 11, 16), "0")], max_move="0.2")


# ---------- ETF quote gating (FB-0214)


def test_fresh_two_sided_quote_is_quote_backed():
    p = quote_at(q(ex=at(10, 0, 0)), at(10, 0, 20), max_age=timedelta(seconds=30))
    assert p.status is PriceStatus.QUOTE_BACKED and p.ok


def test_missing_quote_is_unpriced_never_filled_from_close():
    p = quote_at(None, ts(10, 30), max_age=timedelta(seconds=30))
    assert p.status is PriceStatus.UNPRICED
    assert p.reasons == ("no quote",)


def test_quote_received_after_the_decision_is_unpriced():
    p = quote_at(q(rc=ts(11)), ts(10, 30), max_age=timedelta(minutes=5))
    assert "quote received after the decision" in p.reasons


def test_old_quote_is_unpriced():
    p = quote_at(q(ex=ts(9)), ts(10, 30), max_age=timedelta(minutes=5))
    assert any(r.startswith("quote older than") for r in p.reasons)


def test_crossed_and_locked_books_are_unpriced():
    crossed = quote_at(q(bid="100.10", ask="100.00"), ts(10, 30), max_age=timedelta(minutes=5))
    locked = quote_at(q(bid="100.00", ask="100.00"), ts(10, 30), max_age=timedelta(minutes=5))
    assert "crossed book" in crossed.reasons
    assert "locked book" in locked.reasons


def test_one_sided_size_is_unpriced():
    p = quote_at(q(aq=0), ts(10, 30), max_age=timedelta(minutes=5))
    assert "no size on one side" in p.reasons


def test_buy_fills_reference_at_ask_and_sell_at_bid():
    assert fill_reference(q(), "BUY") == Decimal("100.00")
    assert fill_reference(q(), "SELL") == Decimal("99.50")


def test_unknown_side_is_refused():
    with pytest.raises(PitError):
        fill_reference(q(), "HOLD")


# ---------- iNAV comparability (FB-0207, FB-0210)


def test_premium_computed_from_synchronous_inav():
    prem = inav_premium(q(), Inav(Decimal("99.00"), ts(10)), max_skew=timedelta(seconds=5),
                        max_inav_age=timedelta(minutes=2), decision_at=at(10, 0, 30))
    assert prem.reason is None
    assert prem.fraction == Decimal("99.75") / Decimal("99.00") - 1


def test_asynchronous_inav_is_refused_not_compared():
    prem = inav_premium(q(), Inav(Decimal("99.00"), ts(9, 50)), max_skew=timedelta(seconds=5),
                        max_inav_age=timedelta(hours=1), decision_at=at(10, 0, 30))
    assert prem.fraction is None
    assert prem.reason == "iNAV and quote are not synchronous"


def test_stale_inav_is_refused():
    prem = inav_premium(q(), Inav(Decimal("99.00"), ts(10)), max_skew=timedelta(hours=1),
                        max_inav_age=timedelta(minutes=2), decision_at=ts(12))
    assert prem.reason == "iNAV is stale"


def test_missing_inav_is_refused():
    prem = inav_premium(q(), None, max_skew=timedelta(seconds=5),
                        max_inav_age=timedelta(minutes=2), decision_at=ts(10))
    assert prem.reason == "no iNAV"
