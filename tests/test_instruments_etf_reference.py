from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal as D

import pytest

from financial_brain.instruments.etf_reference import (
    EtfRefError,
    Exposure,
    Series,
    SeriesKind,
    classify,
    executable_fair_gap,
    load_reference,
    premium_anomaly,
    tracking,
)
from financial_brain.instruments.pit import Inav, Quote

IST = timezone(timedelta(hours=5, minutes=30))
T = datetime(2026, 11, 17, 11, 0, tzinfo=IST)
ROW = dict(isin="INF0001", symbol="NIFTYX", benchmark="Nifty 50 TRI", fund_house="A AMC",
           effective_from="2024-01-01", listed_on="2020-01-01", ter="0.05",
           tracking_difference="-0.1", tracking_error="0.02")


@pytest.mark.parametrize("bench,exp", [
    ("Nifty 50 TRI", Exposure.EQUITY_BROAD), ("Nifty Bank TRI", Exposure.EQUITY_SECTOR),
    ("Domestic price of Gold", Exposure.GOLD), ("Unnamed basket", Exposure.OTHER),
    ("NASDAQ 100 TRI", Exposure.INTERNATIONAL), ("Nifty 5 yr Benchmark G-Sec", Exposure.DEBT),
    ("Nifty Alpha Low Vol 30", Exposure.EQUITY_FACTOR),
])
def test_classify(bench, exp):
    assert classify(bench) is exp


def test_load_reference_dated_and_refusals():
    later = dict(ROW, effective_from="2026-01-01", ter="0.04")
    refs, bad = load_reference([ROW, later, {"isin": "X"}], "AMC disclosure 2026-11")
    r = refs["IN:INF0001"]
    assert r.as_of(date(2025, 1, 1)).ter == D("0.05")
    assert r.as_of(date(2026, 6, 1)).ter == D("0.04")
    assert r.as_of(date(2025, 1, 1)).benchmark_is_tri
    assert not r.retail_redemption_path and len(bad) == 1


def test_series_kept_separate():
    d = [date(2026, 1, 1), date(2026, 2, 1)]
    px = Series(SeriesKind.PRICE_RETURN, "E", ((d[0], 100.0), (d[1], 109.0)))
    nav = Series(SeriesKind.NAV_RETURN, "E", ((d[0], 100.0), (d[1], 110.0)))
    tri = Series(SeriesKind.BENCHMARK_TR, "B", ((d[0], 1000.0), (d[1], 1100.0)))
    assert tracking(px, tri) == pytest.approx(-0.01)
    with pytest.raises(EtfRefError):
        tracking(nav, tri)


def q(bid="101.00", ask="101.10", at=T):
    return Quote(D(bid), D(ask), at, at, 100, 100)


def test_anomaly_only_with_fresh_synchronous_inav_inside_window():
    w = (time(9, 30), time(15, 0))
    a = premium_anomaly(q(), Inav(D(100), T), threshold=D("0.005"), decision_at=T, window=w, tz=IST)
    assert a and a.label == "OBSERVATION_NOT_ARBITRAGE"
    assert premium_anomaly(q(), Inav(D(100), T - timedelta(minutes=5)), threshold=D("0.005"),
                           decision_at=T, window=w, tz=IST) is None
    early = T.replace(hour=9, minute=16)
    assert premium_anomaly(q(at=early), Inav(D(100), early), threshold=D("0.005"),
                           decision_at=early, window=w, tz=IST) is None
    assert premium_anomaly(q(), Inav(D(100), T), threshold=D("0.005"), decision_at=T, window=w,
                           tz=IST, underlying_open=False) is None


def test_executable_gap_uses_side():
    g = executable_fair_gap(q(), Inav(D(100), T), "BUY", decision_at=T,
                            max_skew=timedelta(seconds=5), max_inav_age=timedelta(seconds=30))
    assert g == D("0.011")
    assert executable_fair_gap(q(), None, "BUY", decision_at=T, max_skew=timedelta(seconds=5),
                               max_inav_age=timedelta(seconds=30)) is None
