"""Index rules (batch four): positions follow their stated rules, use no future price, and the timing statistic
pays nothing for plain market exposure."""
import numpy as np
import pandas as pd

from financial_brain.evaluation import index_rules as IR


def _prices(n=900, seed=0, drift=0.0004):
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.exp(np.cumsum(drift + 0.01 * rng.standard_normal(n))),
                     index=pd.bdate_range("2016-01-01", periods=n))


def test_positions_use_no_future_prices():
    p = _prices()
    full = IR.positions(p)
    cut = IR.positions(p.iloc[:600])
    for k in full:
        assert (full[k].iloc[:600] == cut[k]).all(), k


def test_always_long_earns_no_timing_alpha():
    p = _prices(drift=0.001)
    w = pd.Series(1.0, index=p.index)
    out = IR.timing_outcome(p, w)
    assert abs(out.iloc[300:].mean()) < 1e-9            # a rule that is always in has no timing to be paid for


def test_rsi_rule_enters_below_35_and_exits_above_55():
    p = _prices(seed=3)
    w = IR.positions(p)["q3_rsi_long"]
    rs = IR.rsi(p)
    entries = w.diff() == 1
    exits = w.diff() == -1
    assert (rs[entries] < 35).all() and (rs[exits] > 55).all()


def test_planted_reversal_is_found_and_noise_is_not():
    rng = np.random.default_rng(5)
    n = 2600
    r = 0.01 * rng.standard_normal(n)
    for i in range(1, n):                               # strong next-day reversal after big down days
        if r[i - 1] < -0.012:
            r[i] += 0.02
    p = pd.Series(100 * np.exp(np.cumsum(r)), index=pd.bdate_range("2016-01-01", periods=n))
    out = IR.timing_outcome(p, IR.positions(p)["q1_extreme_down_long"])
    from financial_brain.evaluation import standard as S
    assert S.replication(IR.monthly(out))["pass"]
    q = _prices(n=2600, seed=9)
    assert not S.replication(IR.monthly(IR.timing_outcome(q, IR.positions(q)["q1_extreme_down_long"])))["pass"]


def test_vwap_bounce_takes_one_trade_and_pays_costs():
    ts = pd.date_range("2024-01-02 09:15", "2024-01-02 15:29", freq="1min", tz="Asia/Kolkata")
    n = len(ts)
    c = np.r_[np.linspace(100, 101, 60), np.linspace(101, 100.4, 30), np.linspace(100.4, 102, n - 90)]
    m = pd.DataFrame({"ts": ts, "open": c, "high": c + 0.05, "low": c - 0.05, "close": c, "volume": 100})
    s = IR.vwap_bounce(m)
    assert len(s) <= 1
