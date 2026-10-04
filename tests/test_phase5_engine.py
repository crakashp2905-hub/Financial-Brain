import numpy as np
import pandas as pd
import pytest

from financial_brain.opportunity import engine as E


def cs(n=300, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "lineage": [f"L{i}" for i in range(n)], "isin": [f"I{i}" for i in range(n)],
        "ticker": [f"T{i}" for i in range(n)], "price": 100.0,
        "adv20": np.exp(rng.normal(np.log(2e8), 1.0, n)),
        "vol_60": rng.uniform(0.15, 0.6, n), "mom_12_1": rng.normal(0, .3, n),
        "dist_52w_high": -rng.uniform(0, .5, n), "ret_20d": rng.normal(0, .08, n)})


def test_composite_is_standardised_and_signed():
    c = cs()
    c.loc[0, ["vol_60", "mom_12_1", "dist_52w_high", "ret_20d"]] = [0.1, 2.0, 0.0, -0.5]
    z = E.composite(c)
    assert abs(z.mean()) < 1e-9 and abs(z.std() - 1) < 1e-9
    assert z.iloc[0] == z.max()          # low vol, strong momentum, at the high, oversold


def test_every_name_gets_a_disposition_and_trades_are_capped():
    d = E.decide(cs(), cfg=E.Config(ic=0.1))
    assert d["disposition"].notna().all()
    assert (d["disposition"] == E.TRADE).sum() <= E.TOP_N
    assert d["weight"].max() <= E.MAX_WEIGHT + 1e-12 and d["weight"].sum() <= E.GROSS_CAP + 1e-9
    assert (d.loc[d["disposition"] != E.TRADE, "weight"] == 0).all()


def test_illiquid_name_is_rejected_for_liquidity_not_alpha():
    c = cs()
    c.loc[5, "adv20"] = 1e4
    d = E.decide(c).set_index("lineage")
    assert d.loc["L5", "disposition"] == E.REJECT_LIQUIDITY


def test_blocked_name_is_rejected_for_event():
    c = cs()
    d = E.decide(c, blocked={"L7"}).set_index("lineage")
    assert d.loc["L7", "disposition"] == E.REJECT_EVENT


def test_trades_have_positive_net_alpha():
    d = E.decide(cs(), cfg=E.Config(ic=0.1))
    t = d[d["disposition"] == E.TRADE]
    assert len(t) and (t["net"] >= E.MIN_NET).all()


def test_tiny_cross_section_is_refused():
    with pytest.raises(E.EngineError):
        E.decide(cs(20))


def test_memory_rows_carry_reasons():
    rows = E.to_memory_rows(E.decide(cs(), blocked={"L7"}))
    assert any(r["reason"] == E.REJECT_EVENT for r in rows)
    assert all(r["disposition"] for r in rows)


def test_at_the_assumed_ic_costs_leave_little():
    d = E.decide(cs())
    assert (d["disposition"] == E.TRADE).sum() <= E.TOP_N
    assert d["net"].max() < d["alpha"].max()
