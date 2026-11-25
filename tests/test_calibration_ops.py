from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from financial_brain.execution.exchange_paper import ExchangePaperOms
from financial_brain.forecasting.calibration_tools import (
    CalError,
    IndexMaster,
    IndexVersion,
    apply_isotonic,
    by_stratum,
    compare_to_baselines,
    isotonic,
    material_change,
    nulls,
    strata_calibrate,
)
from financial_brain.paper.ops import (
    CapitalGateway,
    FeedMode,
    SignalTimeline,
    after_nav_handler,
    before_cutoff_handler,
    desk_metrics,
    desk_pages,
    evidence_grade,
    forecast_to_order,
    options_open_handler,
    replay,
)
from financial_brain.risk.capital_allocator import CapitalAllocator

T = datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)


def test_isotonic_monotone():
    steps = isotonic([0.1, 0.2, 0.3, 0.4, 0.5], [0, 1, 0, 1, 1])
    ps = [p for _, p in steps]
    assert ps == sorted(ps)
    assert apply_isotonic(steps, 0.45) == 1.0
    with pytest.raises(CalError):
        isotonic([], [])


def test_strata_prior_only_and_thin_uncalibrated():
    rows = [("expiry", 0.5, 1, date(2026, 1, i + 1)) for i in range(20)] + \
           [("normal", 0.5, 1, date(2026, 1, 1))] + [("expiry", 0.9, 0, date(2026, 12, 1))]
    out = strata_calibrate(rows, date(2026, 6, 1), min_n=10)
    assert out["normal"] == "UNCALIBRATED" and isinstance(out["expiry"], list)
    assert all(ub < 0.9 for ub, _ in out["expiry"])


def test_nulls_and_baselines():
    n = nulls(["UP", "UP", "DOWN"], [0.01])
    assert n["always_up"]["UP"] == 1 and n["momentum"]["UP"] == 1
    assert compare_to_baselines(0.2, {"freq": 0.25}) == {"freq": pytest.approx(-0.05)}


def test_by_stratum_and_material_change():
    assert by_stratum([{"k": "exp", "m": 1}, {"k": "exp", "m": 3}, {"k": "no", "m": 0}], "k", "m") == {"exp": 2.0, "no": 0.0}
    assert material_change({"vendor": "kite", "signal_ttl": 60}, {"vendor": "kite", "signal_ttl": 30}) == ["signal_ttl"]


def test_index_master_keeps_renamed_and_discontinued():
    m = IndexMaster()
    m.add("IDX1", IndexVersion(date(2010, 1, 1), "CNX IT", "CNXIT", "SECTOR", "t1"))
    m.add("IDX1", IndexVersion(date(2015, 1, 1), "NIFTY IT", "NIFTYIT", "SECTOR", "t1", "RENAMED"))
    m.add("IDX2", IndexVersion(date(2010, 1, 1), "OLD", "OLD", "THEME", None))
    m.add("IDX2", IndexVersion(date(2020, 1, 1), "OLD", "OLD", "THEME", None, "DISCONTINUED"))
    m.coverage = {"IDX1": (date(2010, 1, 1), date(2026, 11, 17)), "IDX2": (date(2010, 1, 1), date(2019, 12, 31))}
    assert m.as_of("IDX1", date(2012, 1, 1)).name == "CNX IT"
    assert m.forecastable(date(2018, 1, 1)) == ["IDX1", "IDX2"]
    assert m.forecastable(date(2021, 1, 1)) == ["IDX1"]


def test_signal_timeline():
    s = SignalTimeline("S")
    s.mark("source", T); s.mark("decision", T + timedelta(milliseconds=150))
    assert s.latency_ms("source", "decision") == 150
    with pytest.raises(ValueError):
        s.mark("receipt", T - timedelta(seconds=1))


def test_feed_mode_and_replay():
    assert evidence_grade(FeedMode.STREAM) == "INSTANT_OK"
    assert evidence_grade(FeedMode.POLLING).startswith("DEGRADED")
    out = replay([{"at": 2, "x": "b"}, {"at": 1, "x": "a"}], lambda e: e["x"])
    assert out == ["a", "b"]


def test_capital_gateway_blocks_conflicting_proposals():
    gw = CapitalGateway(CapitalAllocator(D(1000), {"SWING": D(600), "ETF": D(600)}, {"NIFTY": D(500)}))
    a = gw.apply("risk_allocator", {("SWING", "NIFTY"): D(400)})
    b = gw.apply("optimizer", {("ETF", "NIFTY"): D(400)})
    assert all(a.values()) and not any(b.values())


def test_forecast_to_order():
    oms = ExchangePaperOms(max_quote_age=timedelta(seconds=5))
    assert forecast_to_order(oms, forecast_id="F", eligible=False, token="T", lots=1, lot_size=75,
                             limit=D(100), expires_at=T) is None
    o = forecast_to_order(oms, forecast_id="F", eligible=True, token="T", lots=2, lot_size=75,
                          limit=D(100), expires_at=T + timedelta(minutes=5))
    assert o.units == 150


def test_handlers():
    calls = []
    with pytest.raises(RuntimeError):
        options_open_handler(lambda: False, lambda: calls.append(1))(date(2026, 11, 17))
    with pytest.raises(RuntimeError):
        after_nav_handler(lambda d: False, [])(date(2026, 11, 17))
    after_nav_handler(lambda d: True, [lambda d: calls.append("nav"), lambda d: calls.append("mark")])(date(2026, 11, 17))
    assert calls == ["nav", "mark"]
    h = before_cutoff_handler(lambda d: ["r1"], calls.append, lambda: T, lambda d: T - timedelta(minutes=1))
    with pytest.raises(RuntimeError):
        h(date(2026, 11, 17))


def test_desk_metrics_and_pages():
    m = desk_metrics([10, 20, 30], [100], 1, 100, timedelta(minutes=6), timedelta(hours=6))
    assert m["median_latency_ms"] == 20 and m["drop_rate"] == 0.01 and m["downtime_share"] == pytest.approx(1 / 60)
    p = desk_pages({"ETF": {"status": "OK"}})
    assert p["MUTUAL_FUND"]["clock"] == "daily NAV (not live)" and p["ETF"]["status"] == "OK"
