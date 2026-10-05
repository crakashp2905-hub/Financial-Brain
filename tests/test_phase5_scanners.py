from datetime import date

import numpy as np
import pandas as pd
import pytest

from financial_brain.opportunity import scanners as S


def cs(n=300, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"lineage": [f"L{i}" for i in range(n)], "isin": "I", "ticker": [f"T{i}" for i in range(n)],
                         "price": 100.0, "turnover": 2e8, "adv20": 2e8, "vol_60": rng.uniform(.15, .5, n),
                         "mom_12_1": rng.normal(0, .3, n), "dist_52w_high": -rng.uniform(0, .5, n),
                         "ret_20d": rng.normal(0, .08, n), "ret_1d": 0.0, "adx_14": rng.uniform(10, 40, n),
                         "atr_pctile_250": rng.uniform(0, 1, n), "atr_14_pct": 0.02})


def test_every_opportunity_has_the_standard_fields_and_net_is_return_minus_cost():
    opps = S.scan(cs(), date(2026, 10, 5))
    assert opps and all(o.expected_net == pytest.approx(o.expected_return - o.cost) for o in opps)
    o = opps[0]
    for f in ("instrument", "horizon", "type", "evidence", "expected_risk", "liquidity", "expiry", "invalidation"):
        assert getattr(o, f) is not None
    assert all(o.invalidation < o.price for o in opps) and all(o.expiry > date(2026, 10, 5) for o in opps)


def test_only_measured_scanners_get_an_expected_return():
    opps = S.scan(cs(), date(2026, 10, 5))
    for o in opps:
        if S.EVIDENCE[o.type][0] is None:
            assert o.expected_return == 0.0 and o.expected_net < 0          # pays cost, earns nothing claimed
    assert any(o.expected_return > 0 for o in opps if o.type == "momentum")


def test_the_volatility_expansion_scanner_expects_underperformance():
    v = [o for o in S.scan(cs(), date(2026, 10, 5)) if o.type == "volatility_expansion"]
    assert v and all(o.expected_return < 0 for o in v)


def test_expected_return_uses_the_calibration_shrink():
    o = next(o for o in S.scan(cs(), date(2026, 10, 5)) if o.type == "momentum")
    ic = S.EVIDENCE["momentum"][0]
    assert o.expected_return == pytest.approx(S.SHRINK * ic * o.expected_risk * o.features["z"])


def test_best_per_name_keeps_the_highest_net_and_lists_every_scanner_that_flagged_it():
    b = S.best_per_name(S.scan(cs(), date(2026, 10, 5)))
    assert b["ticker"].is_unique and "flagged_by" in b.columns


def test_events_and_volume_anomalies_are_flagged():
    c = cs()
    c.loc[3, "turnover"] = 10 * c.loc[3, "adv20"]
    opps = S.scan(c, date(2026, 10, 5), events={"L7"})
    assert any(o.type == "volume_anomaly" and o.ticker == "T3" for o in opps)
    assert any(o.type == "event" and o.ticker == "T7" for o in opps)


def test_record_resolve_and_score_by_type():
    import duckdb
    ledger = duckdb.connect()
    research = duckdb.connect()
    research.execute("CREATE TABLE adjusted_prices(lineage VARCHAR, ticker VARCHAR, business_date DATE, close_adj DOUBLE)")
    days = pd.bdate_range("2026-10-05", periods=70).date
    for k, d in enumerate(days):
        research.execute("INSERT INTO adjusted_prices VALUES ('A','T1',?,?), ('B','T2',?,?)",
                         [d, 100 + k, d, 100.0])
    o = S.Opportunity("A", "T1", date(2026, 10, 5), 5, "momentum", 0.01, 0.05, 1e9, 0.003, 0.007, "X", 5.0, "s",
                      date(2026, 10, 12), 90.0, 100.0)
    S.save(ledger, [o], "2026-10-05")
    assert S.resolve(ledger, research) == 1
    sc = S.scorecard_by_type(ledger)[0]
    assert sc["type"] == "momentum" and sc["realised_gross"] == pytest.approx(0.05 / 2, rel=1e-6)
