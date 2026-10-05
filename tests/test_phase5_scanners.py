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


def test_one_model_per_name_a_names_scanners_agree_on_its_20_session_expectation():
    opps = S.scan(cs(), date(2026, 10, 5))
    by = {}
    for o in opps:
        by.setdefault(o.ticker, set()).add(round(o.expected_return / S.HORIZON_SCALE[o.horizon], 10))
    assert all(len(v) == 1 for v in by.values())           # no cherry-picking between a name's scanners


def test_expectations_come_from_the_measured_deciles_and_never_exceed_them():
    opps = S.scan(cs(), date(2026, 10, 5))
    lo, hi = min(S.DECILE_20D.values()), max(S.DECILE_20D.values())
    assert all(lo * max(S.HORIZON_SCALE.values()) - 1e-12 <= o.expected_return <= hi * max(S.HORIZON_SCALE.values()) + 1e-12
               for o in opps)
    assert max(o.expected_return for o in opps) < 0.01      # never the +6% the Grinold extrapolation promised


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


def test_a_zero_volatility_row_is_excluded_not_a_crash():
    c = cs()
    c.loc[0, "vol_60"] = 0.0
    opps = S.scan(c, date(2026, 10, 5))
    assert opps and all(o.ticker != "T0" for o in opps)
