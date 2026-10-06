"""Trader core: the alpha chain, capital allocation, the exit brain, selection regret and evidence tiers."""
import numpy as np
import pandas as pd
import pytest

from financial_brain.decisions import exit_brain as XB
from financial_brain.decisions import opportunity_card as OC
from financial_brain.evaluation import evidence_tiers as ET
from financial_brain.evaluation import regret as RG
from financial_brain.portfolio import next_capital as NC


def test_crowding_penalises_only_what_moves_with_the_book():
    a = OC.alpha_chain(0.02, 0.10, cov_with_book_h=0.002)
    b = OC.alpha_chain(0.02, 0.10, cov_with_book_h=-0.002)
    assert a["portfolio_adjusted_alpha"] < b["portfolio_adjusted_alpha"] == pytest.approx(0.02)
    assert a["risk_adjusted_alpha"] == pytest.approx(0.2)
    assert '"chain"' in OC.FullOpportunity("L", "T", "2026-10-07", "momentum", "long", 60, chain=a).to_json()


def cov3():
    vols = np.array([0.30, 0.30, 0.30, 0.30])
    corr = np.array([[1, .9, .1, .2], [.9, 1, .1, .2], [.1, .1, 1, .2], [.2, .2, .2, 1]])
    names = ["HELD", "TWIN", "DIVERS", "CASHY"]
    return pd.DataFrame(np.outer(vols, vols) * corr, index=names, columns=names)


def test_the_next_lakh_prefers_the_diversifier_over_a_twin_of_what_is_held():
    cands = pd.DataFrame({"ticker": ["TWIN", "DIVERS"], "net": [0.020, 0.018]})
    r = NC.allocate(cands, cov3(), {"HELD": 400_000}, budget=100_000)
    assert r["allocation"].get("DIVERS", 0) > r["allocation"].get("TWIN", 0)
    t = NC.compare(cands, cov3(), {"HELD": 400_000})
    assert t.iloc[0]["ticker"] == "DIVERS" and "moves with what you hold" in NC.reason(t)


def test_no_positive_marginal_value_means_cash():
    r = NC.allocate(pd.DataFrame({"ticker": ["TWIN"], "net": [-0.01]}), cov3(), {"HELD": 1}, budget=50_000)
    assert r["allocation"] == {} and r["cash"] == 50_000


def base(**kw):
    d = dict(composite_now=0.6, composite_entry=0.6, price=105, cost=100, stop=90, above_ma200=True, held_sessions=10,
             horizon=60, atr_pctile=0.5, recent_filing=False, weight=0.1, sector_weight=0.2)
    d.update(kw)
    return XB.decide(**d)


def test_the_exit_brain_takes_the_most_severe_view_and_says_whether_it_is_tested():
    assert base()["action"] == XB.HOLD
    e = base(price=89)
    assert e["action"] == XB.EXIT and e["tested_basis"] and e["reduce_fraction"] == 1.0
    r = base(above_ma200=False, atr_pctile=0.95)
    assert r["action"] == XB.REDUCE and r["reduce_fraction"] == 0.5 and not r["tested_basis"]
    assert base(composite_now=0.85, composite_entry=0.6, price=101)["action"] == XB.ADD
    assert base(recent_filing=True)["action"] == XB.REVIEW


def test_regret_separates_making_money_from_choosing_well():
    c = pd.DataFrame({"day": ["d1"] * 3 + ["d2"] * 3, "ticker": list("ABCABC"), "chosen": [True, False, False] * 2,
                      "realised": [0.02, 0.07, 0.01, 0.03, 0.01, 0.02]})
    days = RG.by_day(c)
    s = RG.summary(days)
    assert days.iloc[0]["missed"] == "B" and days.iloc[0]["regret"] == pytest.approx(0.05)
    assert days.iloc[1]["missed"] is None and s["hit"] == 0.5


def test_evidence_tiers_never_rate_a_backtest_as_confidence():
    led = {x["strategy"]: x for x in ET.ledger()}
    assert led["ORB intraday"]["confidence"] == "NONE"
    assert led["learned model (challenger)"]["confidence"] == "LIMITED"      # passed walk-forward, paper record empty
    assert all(x["tiers"][ET.LIVE]["n"] == 0 for x in led.values())


def test_a_trip_is_compared_with_the_list_from_the_evening_before(tmp_path):
    import duckdb
    from datetime import date
    con = duckdb.connect()
    con.execute("CREATE TABLE adjusted_prices (ticker VARCHAR, business_date DATE, close_adj DOUBLE)")
    for t, a, b in (("A", 100, 110), ("B", 100, 130), ("X", 100, 102)):
        con.executemany("INSERT INTO adjusted_prices VALUES (?,?,?)", [(t, date(2026, 10, 7), a), (t, date(2026, 10, 9), b)])
    trips = pd.DataFrame({"symbol": ["X"], "entry_day": [date(2026, 10, 7)], "exit_day": [date(2026, 10, 9)], "ret": [0.02]})
    lists = pd.DataFrame({"day": ["2026-10-06", "2026-10-06"], "rank": [1, 2], "ticker": ["A", "B"], "net": [0.005, 0.004]})
    r = RG.journal_vs_list(trips, lists, con).iloc[0]
    assert r["list_best"] == "B" and r["list_best_ret"] == pytest.approx(0.30) and not r["on_list"]
