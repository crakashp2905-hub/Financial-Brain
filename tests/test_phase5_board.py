import duckdb
import numpy as np
import pandas as pd
import pytest

from financial_brain.paper import board as B


def daily(n=12):
    return pd.DataFrame({"ticker": [f"S{i}" for i in range(n)], "z": np.linspace(2, -2, n),
                         "alpha": np.linspace(0.01, -0.01, n), "cost": 0.003, "net": np.linspace(0.007, -0.013, n),
                         "vol_60": 0.25, "adv20": 3e9})


def quotes(n=12):
    q = {f"S{i}": {"price": 100.0, "open": 100.0, "high": 101.0, "low": 99.0, "prev_close": 100.0} for i in range(n)}
    q["S5"]["open"] = 103.0                          # a 3% gap up
    return q


def test_ranks_by_net_alpha_marks_the_top_five_and_what_clears():
    b = B.build(quotes(), daily())
    assert list(b["symbol"][:5]) == ["S0", "S1", "S2", "S3", "S4"] and b["top"].sum() == 5
    assert set(b[b["clears"]]["symbol"]) == {"S0", "S1"}           # net above the 0.5% hurdle
    assert (b["shares"] >= 0).all() and (b["stop"] < b["price"]).all()


def test_setups_are_recorded_but_never_rank_a_name():
    b = B.build(quotes(), daily()).set_index("symbol")
    assert "GAP_UP" in b.loc["S5", "setups"] and "ORB_PENDING" in b.loc["S0", "setups"]
    assert b.loc["S5", "rank"] == 6                                  # its gap does not move it up


def test_a_day_where_nothing_clears_says_so_and_still_lists_a_watch_list():
    d = daily()
    d["net"] = -0.002
    text = B.render(B.build(quotes(), d), "2026-10-06")
    assert "Nothing clears costs today" in text and "| WATCH |" in text and "| TRADE" not in text


def test_record_and_resolve_the_same_day_and_score_the_top_against_the_rest():
    con = duckdb.connect()
    for k, day in enumerate(("2026-10-06", "2026-10-07", "2026-10-08")):
        b = B.build(quotes(), daily())
        B.save(con, b, day, "live")
        closes = {f"S{i}": 101.0 if i < 5 else 100.0 + 0.01 * k for i in range(12)}   # the top five gain 1%
        assert B.resolve_close(con, day, "live", closes) == 12
    sc = {r["horizon"]: r for r in B.scorecard(con)}["same day (09:30 to close)"]
    assert sc["days"] == 3 and sc["spread"] > 0.009 and sc["top"] == pytest.approx(0.01)


def test_errors():
    with pytest.raises(B.BoardError):
        B.build(quotes(), pd.DataFrame())
    with pytest.raises(B.BoardError):
        B.build({"ZZZ": {"price": 1.0}}, daily())
