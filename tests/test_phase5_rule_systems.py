"""Rules as trading systems: positions decided at the close, costs on every change, money management that can ruin."""
import numpy as np
import pandas as pd

from financial_brain.evaluation import rule_systems as RS


def bars(n=600, seed=0, drift=0.0005):
    rng = np.random.default_rng(seed)
    c = 100 * np.cumprod(1 + drift + rng.normal(0, 0.015, n))
    o = np.concatenate([[100.0], c[:-1]])
    return pd.DataFrame({"d": pd.bdate_range("2020-01-01", periods=n).date, "o": o, "h": np.maximum(o, c) * 1.005,
                         "l": np.minimum(o, c) * 0.995, "c": c, "v": rng.uniform(1e5, 1e6, n)})


def test_every_rule_gives_a_long_or_flat_position_with_no_look_ahead():
    b = bars()
    full, cut = RS.rules(b), RS.rules(b.iloc[:400])
    for name, pos in full.items():
        assert set(np.unique(pos.dropna())) <= {0.0, 1.0}, name
        assert np.allclose(pos.iloc[:400].to_numpy(), cut[name].to_numpy(), equal_nan=True), name


def test_costs_are_charged_on_every_change_and_the_universe_is_the_benchmark():
    b = bars().assign(bucket="mega")
    uni = pd.DataFrame({"d": b["d"], "lineage": "L"})
    daily = RS.backtest({"L": b}, uni)
    s = RS.summarise(daily, b["d"].iloc[300])
    assert set(s["rule"]) == set(RS.rules(b))
    one = daily[daily["rule"] == "macd_cross"]
    assert (one["port"] <= one["universe"].abs() + 1).all()


def test_a_martingale_is_ruined_by_a_losing_streak_an_anti_martingale_is_not():
    r = np.array([0.01] * 5 + [-0.012] * 12 + [0.01] * 5)
    assert RS.martingale(r, base=0.01)["ruined"] is True
    assert RS.martingale(r, base=0.01, anti=True)["ruined"] is False


def test_scale_trader_buys_falls_and_sells_rises():
    c = np.array([100, 98, 96, 94, 96, 98, 100.0])
    e = RS.scale_trader(c, step=0.02, unit=0.1)
    assert e[3] > e[0] and e[-1] < e[3]
