from datetime import date, timedelta

import duckdb
import pytest

from financial_brain.risk import stress


def _dates(n: int, start: date = date(2025, 1, 1)) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def test_rolling_compounds_and_worst_windows_reports_short_history():
    assert stress.rolling([-0.1, -0.1, 0.2], 2) == pytest.approx([-0.19, 0.08])
    assert stress.rolling([0.1], 1) == [0.1]
    short = stress.worst_windows({"returns": [0.01] * 10, "dates": _dates(10)}, windows=(1, 5))
    assert short[1]["note"] == "too little history for this window"
    assert short[5]["note"] == "too little history for this window"


def test_worst_windows_uses_compounded_tail_and_constituent_coverage():
    returns = [-0.01] * 25 + [0.02] * 25
    series = {"returns": returns, "dates": _dates(len(returns)), "coverage": [0.8, 1.0] * 25}
    result = stress.worst_windows(series, windows=(1, 5), top=2)
    assert result[1]["n"] == 50
    assert result[1]["minimum_constituent_coverage"] == 0.8
    assert result[1]["incomplete_constituent_days"] == 25
    assert result[1]["expected_shortfall"] == pytest.approx(-0.01)
    assert result[5]["worst"][0]["return"] == pytest.approx((1 - 0.01) ** 5 - 1)


def test_book_returns_normalizes_over_available_constituents_and_empty_weights():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE adjusted_prices (business_date DATE, isin VARCHAR, close_adj DOUBLE)")
    days = _dates(4)
    con.executemany("INSERT INTO adjusted_prices VALUES (?,?,?)", [
        (days[0], "A", 100.0), (days[1], "A", 90.0), (days[2], "A", 99.0),
        (days[3], "A", 99.0), (days[1], "B", 100.0), (days[2], "B", 120.0),
        (days[3], "B", 120.0),
    ])
    result = stress.book_returns(con, {"A": 0.6, "B": 0.4}, end=days[-1], sessions=10)
    assert result["returns"] == pytest.approx([-0.1, 0.14, 0.0])
    assert result["coverage"] == pytest.approx([0.6, 1.0, 1.0])
    assert stress.book_returns(con, {}, end=days[-1]) == {"dates": [], "returns": [], "coverage": []}
    con.close()


def test_conditional_liquidity_reports_short_history_and_measured_crisis_turnover():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE index_levels (business_date DATE, index_name VARCHAR, close_level DOUBLE)")
    con.execute("CREATE TABLE adjusted_prices (business_date DATE, isin VARCHAR, turnover DOUBLE)")
    short_days = _dates(40)
    con.executemany("INSERT INTO index_levels VALUES (?,?,?)",
                    [(d, "Nifty 500", 100 + i) for i, d in enumerate(short_days)])
    assert "only 39 index sessions" in stress.conditional_liquidity(
        con, ["A"], end=short_days[-1])["note"]

    con.execute("DELETE FROM index_levels")
    days = _dates(130)
    levels = [100.0]
    for i in range(1, len(days)):
        levels.append(levels[-1] * (0.97 if i >= 120 else 1.001))
    con.executemany("INSERT INTO index_levels VALUES (?,?,?)",
                    [(d, "Nifty 500", level) for d, level in zip(days, levels)])
    con.executemany("INSERT INTO adjusted_prices VALUES (?,?,?)", [
        (d, "A", 50.0 if i >= 120 else 100.0) for i, d in enumerate(days)])
    result = stress.conditional_liquidity(con, ["A"], end=days[-1])
    assert result["names"] == 1
    assert result["turnover_ratio_median"] == pytest.approx(0.5, abs=0.01)
    assert "spread half" in result["caveat"]
    assert stress.conditional_liquidity(con, [], end=days[-1]) == {}
    con.close()


def test_conditional_vol_handles_short_series_and_regime_vix_buckets():
    short = stress.conditional_vol(None, {"dates": _dates(20), "returns": [0.01] * 20},
                                   end=date(2025, 2, 1))
    assert short["note"] == "only 20 sessions"

    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE index_levels (business_date DATE, index_name VARCHAR, close_level DOUBLE)")
    con.execute("CREATE TABLE market_regime (business_date DATE, version VARCHAR, regime VARCHAR)")
    days = _dates(120)
    returns = [0.001 * (i % 3 - 1) * (1 + i // 40) for i in range(len(days))]
    series = {"dates": days, "returns": returns}
    con.executemany("INSERT INTO index_levels VALUES (?,?,?)", [
        (d, "India VIX", float(i + 1)) for i, d in enumerate(days)])
    con.executemany("INSERT INTO market_regime VALUES (?,?,?)", [
        (d, "v1", "RISK_ON" if i < 60 else "RISK_OFF") for i, d in enumerate(days)])
    result = stress.conditional_vol(con, series, end=days[-1])
    assert set(result["by_vix"]) == {"vix_low", "vix_mid", "vix_high"}
    assert result["by_vix"]["vix_low"]["n"] == 40
    assert set(result["by_regime"]) == {"RISK_ON", "RISK_OFF"}
    assert result["vix_vol_multiplier"] > 0
    con.close()


def test_stress_report_includes_tail_loss_and_capital_translation():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE adjusted_prices (business_date DATE, isin VARCHAR, close_adj DOUBLE, turnover DOUBLE)")
    con.execute("CREATE TABLE index_levels (business_date DATE, index_name VARCHAR, close_level DOUBLE)")
    con.execute("CREATE TABLE market_regime (business_date DATE, version VARCHAR, regime VARCHAR)")
    days = _dates(130)
    prices = []
    indices = []
    regimes = []
    close = 100.0
    for i, d in enumerate(days):
        if i:
            close *= 0.99 if i in (100, 101) else 1.001
        prices.append((d, "A", close, 100.0))
        indices.append((d, "Nifty 500", 100.0 if i == 0 else
                        indices[-2][2] * (0.97 if i >= 120 else 1.001)))
        indices.append((d, "India VIX", float(10 + (i % 30))))
        regimes.append((d, "v1", "RISK_ON" if i < 90 else "RISK_OFF"))
    con.executemany("INSERT INTO adjusted_prices VALUES (?,?,?,?)", prices)
    con.executemany("INSERT INTO index_levels VALUES (?,?,?)", indices)
    con.executemany("INSERT INTO market_regime VALUES (?,?,?)", regimes)
    result = stress.report(con, {"A": 1.0}, end=days[-1], capital_inr=1_000_000)
    assert result["names"] == 1
    assert result["sessions"] == 129
    assert 21 in result["windows"] and 63 in result["windows"]
    assert result["expected_shortfall_inr_21d"] < 0
    assert result["conditional_vol"]["by_regime"]
    assert result["conditional_liquidity"]["names"] == 1
    assert stress.report(con, {}, end=days[-1]) == {"note": "no return history for these names"}
    con.close()
