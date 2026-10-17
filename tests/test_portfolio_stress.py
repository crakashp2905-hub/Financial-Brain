"""Stress tests replay real episodes on the held names and say which names were proxied."""
import numpy as np
import pandas as pd

from financial_brain.portfolio import stress as ST


def test_replay_proxy_and_shocks():
    idx = pd.bdate_range("2019-01-01", "2023-01-01")
    index = pd.Series(np.where(idx < pd.Timestamp("2020-03-01"), 100.0, 70.0), index=idx)      # -30% in COVID
    a = pd.Series(np.where(idx < pd.Timestamp("2020-03-01"), 50.0, 25.0), index=idx)           # -50%
    book = pd.DataFrame({"ticker": ["A", "NEW"], "value_inr": [1000.0, 1000.0], "beta": [1.0, 2.0],
                         "sector": ["S1", "S1"], "adv_inr": [1e6, 1e4]})
    r = ST.run(book, {"A": a}, index)
    c = r["scenarios"]["covid_crash"]
    assert c["proxied"] == ["NEW"] and abs(c["pnl_inr"] - (-500 - 600)) < 1e-6
    assert abs(r["scenarios"]["market_down_20"]["pnl_inr"] - (-200 - 400)) < 1e-6
    assert abs(r["scenarios"]["sector_rotation"]["pnl_inr"] - (-600 - 300)) < 1e-6
    assert r["liquidation_sessions"]["worst_name"] == "NEW" and r["liquidation_sessions"]["worst"] == 2.0
    assert r["worst"][0] == "covid_crash"
