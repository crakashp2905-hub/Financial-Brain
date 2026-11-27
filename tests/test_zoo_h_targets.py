from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from financial_brain.evaluation import edges as E
from financial_brain.paper import zoo_edges as ZE
from financial_brain.evaluation.edges import load as load_edge_panel


def _panel(n_names=40, n_days=300):
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2025-01-01", periods=n_days)
    cols = [f"L{i}" for i in range(n_names)]
    drift = np.r_[np.full(10, -0.004), np.linspace(0.001, 0.003, n_names - 10)]
    r = rng.normal(drift, 0.01, size=(n_days, n_names))
    C = pd.DataFrame(100 * np.exp(np.cumsum(r, axis=0)), index=idx, columns=cols)
    U = pd.DataFrame(True, index=idx, columns=cols)
    return {"C": C, "U": U}, cols


def _rcon(cols):
    con = duckdb.connect()
    ind = ["IND_A"] * 10 + ["IND_B"] * 10 + ["IND_C"] * 10 + ["IND_D"] * 10
    con.register("ap", pd.DataFrame({"lineage": cols, "isin": [f"IN{i}" for i in range(len(cols))],
                                      "business_date": pd.Timestamp("2025-01-01")}))
    con.execute("CREATE TABLE adjusted_prices AS SELECT * FROM ap")
    memberships = []
    for isin, industry in zip([f"IN{i}" for i in range(len(cols))], ind):
        memberships.extend([
            (isin, industry, "NIFTY 500", pd.Timestamp("2025-01-01"), pd.Timestamp("2025-01-02", tz="UTC")),
            (isin, "FUTURE_INDUSTRY", "NIFTY 500", pd.Timestamp("2026-03-01"), pd.Timestamp("2026-03-02", tz="UTC")),
        ])
    con.register("im", pd.DataFrame(memberships, columns=["isin", "industry", "index_name", "as_of", "observed_at"]))
    con.execute("CREATE TABLE index_membership AS SELECT * FROM im")
    return con


def test_h003_and_h005_pick_twenty_equal_weighted_names():
    P, cols = _panel()
    out = ZE.h_targets(_rcon(cols), P)
    h3, h5 = out["edge_h003_riskadj_mom"], out["edge_h005_industry_mom"]
    assert len(h3) == 20 and abs(sum(h3.values()) - 1) < 1e-9
    # H005 holds only names from the three best industries (the drift rises with the column index)
    assert h5 and set(h5) <= set(cols[10:])
    assert abs(sum(h5.values()) - 1) < 1e-9


def test_h005_data_failure_does_not_suppress_h003_target():
    P, cols = _panel()
    con = _rcon(cols)
    original = ZE._industries

    def fail_industries(*args, **kwargs):
        raise ValueError("missing industry membership")

    ZE._industries = fail_industries
    try:
        out = ZE.h_targets(con, P)
    finally:
        ZE._industries = original

    assert len(out["edge_h003_riskadj_mom"]) == 20
    assert "edge_h005_industry_mom" not in out
    assert "missing industry membership" in out["_errors"]["edge_h005_industry_mom"]


def test_monthly_p2_history_failure_does_not_suppress_other_books(monkeypatch):
    P, cols = _panel()
    con = _rcon(cols)
    last = P["C"].index[-1]
    monkeypatch.setattr(ZE, "_panel", lambda *_args: P)
    monkeypatch.setattr(E, "residual_scores", lambda panel: panel["C"].rank(axis=1))
    monkeypatch.setattr(E, "momentum_scores", lambda panel: panel["C"].rank(axis=1))
    monkeypatch.setattr(E, "info_discreteness", lambda panel: panel["C"].rank(axis=1))
    monkeypatch.setattr(E, "top_picks", lambda *_args: (_ for _ in ()).throw(ValueError("missing P2 history")))

    out = ZE.monthly_targets(con, last.date())

    assert "edge_p2_volmanaged_mom" not in out
    assert "missing P2 history" in out["_errors"]["edge_p2_volmanaged_mom"]
    assert {"edge_p1_residual_mom", "edge_p3_frog_in_pan", "edge_h003_riskadj_mom",
            "edge_h005_industry_mom"} <= out.keys()


def test_h005_skips_industries_with_fewer_than_eight_names():
    P, cols = _panel()
    con = _rcon(cols)
    con.execute("UPDATE index_membership SET industry = 'TINY' WHERE isin IN ('IN39', 'IN38')")
    out = ZE.h_targets(con, P)
    assert not {"L38", "L39"} & set(out["edge_h005_industry_mom"])


def test_h005_uses_only_industry_membership_known_at_rebalance():
    P, cols = _panel()
    con = _rcon(cols)
    as_of = P["C"].index[-1].date()
    industries = ZE._industries(con, cols, as_of)
    assert industries["L0"] == "IND_A"
    assert "FUTURE_INDUSTRY" not in set(industries)


def test_momentum_equity_universe_excludes_etfs_and_mutual_fund_units():
    con = duckdb.connect()
    day = pd.Timestamp("2026-11-24")
    con.register("prices", pd.DataFrame({
        "business_date": [day] * 3,
        "lineage": ["STOCK", "ETF", "MF"],
        "isin": ["INE000A01001", "INF000A01001", "INF000B01001"],
        "close_adj": [100.0, 100.0, 100.0],
    }))
    con.execute("CREATE TABLE adjusted_prices AS SELECT * FROM prices")
    con.register("features_input", pd.DataFrame({
        "business_date": [day] * 3,
        "lineage": ["STOCK", "ETF", "MF"],
        "adv20": [100.0, 1_000_000.0, 500_000.0],
    }))
    con.execute("CREATE TABLE features AS SELECT * FROM features_input")
    con.register("universe_input", pd.DataFrame({
        "business_date": [day] * 3,
        "isin": ["INE000A01001", "INF000A01001", "INF000B01001"],
        "exchange": ["NSE"] * 3,
        "series": ["EQ"] * 3,
        "instrument_type": ["STK", "ETF", "MF"],
        "tradable": [True] * 3,
    }))
    con.execute("CREATE TABLE universe_snapshots AS SELECT * FROM universe_input")

    panel = load_edge_panel(con, start="2026-11-24", n=1)

    assert list(panel["C"].columns) == ["STOCK"]
    assert panel["U"].loc[day, "STOCK"]
