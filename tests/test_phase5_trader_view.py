"""The trader view: why this, why now, edge, if wrong, how to enter, case against, watched but not traded."""
import numpy as np
import pandas as pd
import pytest

from financial_brain.paper import trader_view as TV


def cs(n=200, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"ticker": [f"T{i}" for i in range(n)], "price": rng.uniform(100, 1000, n),
                         "vol_60": rng.uniform(0.15, 0.6, n), "mom_12_1": rng.normal(0.1, 0.3, n),
                         "dist_52w_high": -rng.uniform(0, 0.4, n), "ret_20d": rng.normal(0, 0.08, n),
                         "turnover": rng.uniform(1e7, 5e8, n), "adv20": rng.uniform(1e7, 5e8, n),
                         "rsi_14": rng.uniform(20, 85, n), "atr_pctile_250": rng.uniform(0, 1, n)})


def test_why_this_names_what_lifted_and_what_held_back_the_rank():
    c = cs()
    comp = TV.components(c)
    i = comp.sum(axis=1).idxmax()
    lines = TV.why_this(c.loc[i], comp.loc[i], rank=1, universe=len(c))
    assert lines[0].startswith("ranked 1 of 200") and any(l.startswith("lifted by") for l in lines)


def test_why_now_flags_an_extended_overbought_name():
    row = pd.Series({"dist_52w_high": -0.01, "mom_12_1": 0.5, "turnover": 3e8, "adv20": 1e8, "ret_20d": 0.25,
                     "rsi_14": 78, "atr_pctile_250": 0.95})
    checks = dict((t.split(" - ")[-1], ok) for ok, t in TV.why_now(row))
    assert checks["extended"] is False and checks["overbought"] is False
    assert any(ok for ok, _ in TV.why_now(row))


def test_if_wrong_the_stop_caps_the_loss_and_reward_risk_is_gain_over_loss():
    r = np.array([-0.30, -0.05, -0.02, 0.03, 0.06, 0.09])
    o = TV.outcome(r, stop_pct=0.10)
    assert o["loss_if_wrong"] == pytest.approx((0.10 + 0.05 + 0.02) / 3)
    assert o["gain_if_right"] == pytest.approx(0.06) and o["p_up"] == pytest.approx(0.5)
    assert o["reward_risk"] == pytest.approx(0.06 / ((0.17) / 3))


def test_entry_band_sits_around_the_price_and_chasing_is_above_it():
    row = pd.Series({"price": 100.0, "vol_60": 0.32})
    ex = TV.execution(row, {"spread": 0.0008, "impact": 0.0004})
    assert ex["band"][0] < 100 < ex["band"][1] < ex["dont_chase"]
    assert ex["slippage_bp"] == pytest.approx(6.0)


def test_watched_not_traded_explains_cost_rejections():
    best = pd.DataFrame({"ticker": ["A", "B", "C"], "type": ["momentum"] * 3, "expected_return": [0.004, 0.009, 0.002],
                         "expected_net": [0.001, -0.002, -0.003], "cost": [0.003, 0.011, 0.005],
                         "alpha": [{"costs": 0.003}, {"costs": 0.011, "fees": 0.003, "impact": 0.006}, {"costs": 0.005}]})
    w = TV.watched_not_traded(best, {"A"})
    assert w[0]["ticker"] == "B" and "exceed" in w[0]["why"]


def test_the_edge_panel_says_weak_in_words_when_the_evidence_is_weak():
    row = pd.Series({})
    p = dict((k, v) for k, v, _ in TV.edge_panel(row, decile_mean=0.0015, analogue=None, breadth_state="narrow",
                                                    fno=None, bucket="mid", ep_pct=None))
    assert p["Signal (composite decile)"] == "Weak" and p["Historical analogues"] == "n.a." and p["Market regime"] == "Weak"


def test_the_sizing_data_cannot_break_out_of_its_script_tag():
    html = TV.calculator([{"id": "r1", "price": 1.0, "stop": 0.9, "adv": 1e6, "x": "</script><b>"}])
    assert "</script><b>" not in html


def test_fundamentals_show_their_age():
    p = dict((k, d) for k, _, d in TV.edge_panel(pd.Series({}), decile_mean=None, analogue=None, breadth_state=None, fno=None,
                                                 bucket="mega", ep_pct=None,
                                                 fund={"pe": 13.75, "pb": 1.7, "ev_ebitda": 11.5, "roe": 0.08,
                                                       "debt_equity": 0.35, "latest_period": "2024-12-31"}))
    assert "P/E 13.8" in p["Fundamental"] and "ROE 8%" in p["Fundamental"] and "stale" in p["Fundamental"]
