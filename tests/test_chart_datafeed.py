from datetime import datetime, timezone

import duckdb
import pytest

from financial_brain.interfaces.chart_datafeed import ChartDataError, configuration, history, symbol_info


def db():
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE minute_bars (
        instrument_token BIGINT, tradingsymbol VARCHAR, ts TIMESTAMPTZ, open DOUBLE, high DOUBLE,
        low DOUBLE, close DOUBLE, volume BIGINT)""")
    con.execute("""INSERT INTO minute_bars VALUES
        (1, 'INFY', '2026-10-30 09:15:00+05:30', 100, 102, 99, 101, 500),
        (1, 'INFY', '2026-10-30 09:16:00+05:30', 101, 103, 100, 102, 600),
        (2, 'TCS', '2026-10-30 09:15:00+05:30', 200, 201, 199, 200, 700)""")
    return con


def test_history_is_ordered_utc_and_token_isolated():
    con = db()
    start = int(datetime(2026, 10, 30, 3, 45, tzinfo=timezone.utc).timestamp())
    result = history(con, token=1, resolution="1", start=start, end=start + 120)
    assert result == {"s": "ok", "t": [start, start + 60],
                      "o": [100, 101], "h": [102, 103], "l": [99, 100],
                      "c": [101, 102], "v": [500, 600]}
    assert history(con, token=1, resolution="1", start=start + 120, end=start + 180) == {"s": "no_data"}
    con.close()


@pytest.mark.parametrize("kwargs", [
    {"token": 0}, {"token": True}, {"resolution": "5"},
    {"start": -1}, {"end": 0}, {"end": 8 * 86400},
])
def test_bad_requests_fail_closed(kwargs):
    con = db()
    with pytest.raises(ChartDataError):
        history(con, **{"token": 1, "resolution": "1", "start": 0, "end": 60, **kwargs})
    con.close()


def test_corrupt_bar_rejected():
    con = db()
    con.execute("UPDATE minute_bars SET high = 98 WHERE instrument_token = 1")
    start = int(datetime(2026, 10, 30, 3, 45, tzinfo=timezone.utc).timestamp())
    with pytest.raises(ChartDataError):
        history(con, token=1, resolution="1", start=start, end=start + 120)
    con.close()


def test_symbol_resolution_rejects_recycled_or_ambiguous_token():
    con = db()
    assert symbol_info(con, 1)["timezone"] == "Asia/Kolkata"
    con.execute("""INSERT INTO minute_bars VALUES
        (1, 'OTHER', '2026-10-30 09:17:00+05:30', 100, 101, 99, 100, 1)""")
    with pytest.raises(ChartDataError):
        symbol_info(con, 1)
    con.close()


def test_udf_app_builds_read_only_routes(tmp_path):
    pytest.importorskip("fastapi")
    from financial_brain.interfaces.chart_udf_api import build_app

    path = tmp_path / "chart.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE minute_bars (instrument_token BIGINT)")
    con.close()
    app = build_app(path)
    routes = {route.path: route.methods for route in app.routes}
    assert set(routes) == {"/config", "/symbols", "/search", "/history"}
    assert all(methods == {"GET"} for methods in routes.values())
    assert configuration()["supported_resolutions"] == ["1"]
