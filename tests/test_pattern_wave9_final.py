"""Final wave-9 tests: point-in-time valid universe on temporary databases, readable static charts, and the
completion-time boundary (CP-0156, CP-0262, CP-0345). Every database here is built in a temporary folder."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pytest

from financial_brain.patterns import marketdata
from financial_brain.patterns.chart_svg import render_candles_svg
from financial_brain.patterns.detectors import Bars
from financial_brain.patterns.pit_universe import fno_stock_symbols, valid_universe


def _dbs(tmp_path: Path) -> tuple[duckdb.DuckDBPyConnection, duckdb.DuckDBPyConnection]:
    uni = duckdb.connect(str(tmp_path / "uni.duckdb"))
    uni.execute(
        "CREATE TABLE universe_snapshots (business_date DATE, isin VARCHAR, exchange VARCHAR, ticker VARCHAR, "
        "series VARCHAR, instrument_type VARCHAR, traded_volume DOUBLE, turnover DOUBLE, trades DOUBLE, "
        "close_price DOUBLE, tradable BOOLEAN)"
    )
    d1, d2 = date(2026, 1, 5), date(2026, 1, 6)
    uni.executemany(
        "INSERT INTO universe_snapshots VALUES (?, ?, 'NSE', ?, 'EQ', 'STK', 1, ?, 1, 100, ?)",
        [
            (d1, "I1", "AAA", 5e6, True),
            (d1, "I2", "BBB", 1e6, True),
            (d1, "I3", "CCC", 9e6, False),
            (d1, "I4", "DDD", 2e6, True),  # tradable but no stock futures
            (d2, "I1", "AAA", 6e6, True),
        ],
    )
    fo = duckdb.connect(str(tmp_path / "fo.duckdb"))
    fo.execute(
        "CREATE TABLE fo_bhav (trade_date DATE, instrument VARCHAR, symbol VARCHAR, expiry DATE, strike DOUBLE, "
        "option_type VARCHAR)"
    )
    fo.executemany(
        "INSERT INTO fo_bhav VALUES (?, 'FUTSTK', ?, DATE '2026-01-29', 0, 'XX')",
        [(d1, "AAA"), (d1, "BBB"), (d2, "AAA")],
    )
    fo.execute("INSERT INTO fo_bhav VALUES (?, 'FUTIDX', 'NIFTY', DATE '2026-01-29', 0, 'XX')", [d1])
    return uni, fo


def test_fno_membership_uses_stock_futures_on_that_date_only(tmp_path: Path) -> None:
    _uni, fo = _dbs(tmp_path)
    assert fno_stock_symbols(fo, date(2026, 1, 5)) == {"AAA", "BBB"}
    assert fno_stock_symbols(fo, date(2026, 1, 6)) == {"AAA"}  # index futures are not stock members


def test_valid_universe_excludes_non_tradable_and_non_fno_names(tmp_path: Path) -> None:
    uni, fo = _dbs(tmp_path)
    rows = valid_universe(uni, fo, date(2026, 1, 5))
    assert [r["ticker"] for r in rows] == ["AAA", "BBB"]  # CCC is not tradable; DDD has no F&O that day
    assert all(r["fno_member"] for r in rows)


def test_valid_universe_can_keep_names_without_fno_when_asked(tmp_path: Path) -> None:
    uni, fo = _dbs(tmp_path)
    rows = valid_universe(uni, fo, date(2026, 1, 5), require_fno=False)
    flags = {r["ticker"]: r["fno_member"] for r in rows}
    assert flags == {"AAA": True, "BBB": True, "DDD": False}


def test_valid_universe_is_point_in_time_per_date(tmp_path: Path) -> None:
    uni, fo = _dbs(tmp_path)
    assert [r["ticker"] for r in valid_universe(uni, fo, date(2026, 1, 6))] == ["AAA"]


@pytest.mark.parametrize("n", [20, 500])
def test_static_chart_has_one_candle_per_bar_and_fixed_ratio(n: int) -> None:
    closes = [100.0 + ((i % 7) - 3) * 0.5 for i in range(n)]
    bars = Bars.from_closes(closes, spread=0.4)
    svg = render_candles_svg(bars, width_px=720, height_px=360, title="demo")
    assert svg.count("<rect ") == n
    assert 'viewBox="0 0 720 360"' in svg
    assert f'data-bars="{n}"' in svg
    assert "SYNTHETIC OR RESEARCH ONLY" in svg


def test_static_chart_escapes_its_title_and_refuses_empty_input() -> None:
    bars = Bars.from_closes([100.0, 101.0, 102.0], spread=0.2)
    svg = render_candles_svg(bars, title="<script>x</script>")
    assert "<script>" not in svg
    with pytest.raises(ValueError):
        render_candles_svg(Bars((), (), (), ()))


def test_bar_completing_exactly_at_the_query_time_is_usable() -> None:
    rows = [{"bar": "09:15-09:20", "completed_at_utc": "2026-01-05T03:50:00+00:00"}]
    assert marketdata.bars_completed_by(rows, "2026-01-05T03:50:00+00:00") == rows  # CP-0345 boundary


def test_a_close_exactly_at_the_breakout_level_does_not_confirm() -> None:
    from financial_brain.patterns.detectors import _const, _resolve

    bars = Bars.from_closes([100.0, 101.0], spread=0.2)
    assert _resolve(bars, 0, up=_const(101.0)) == {}  # equal is not beyond
    assert _resolve(bars, 0, up=_const(100.5)) == {"confirmed_index": 1, "confirmed_direction": "bullish"}


def test_a_close_exactly_at_the_failure_level_does_not_fail() -> None:
    from financial_brain.patterns.detectors import _resolve

    bars = Bars.from_closes([100.0, 99.0], spread=0.2)
    assert _resolve(bars, 0, fail_down=99.0) == {}
    assert _resolve(bars, 0, fail_down=99.5) == {"failed_index": 1}


def test_cup_is_not_emitted_before_its_right_lip_is_known() -> None:
    """With a handle shorter than the pivot confirmation window the right lip is not yet known; nothing may emit."""
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("fixtures_builder", root / "scripts" / "build_pattern_fixtures.py")
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    from financial_brain.patterns.detectors2 import cup_handle

    bars = builder.to_bars(builder.mid_bars(builder._cup_positive(), 0.3))
    assert cup_handle(bars) != []  # the full-length handle is found
    assert cup_handle(bars, handle_len=1) == []  # right lip confirms two bars after the peak, after the handle ends
