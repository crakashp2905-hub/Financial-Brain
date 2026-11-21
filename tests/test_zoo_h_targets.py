from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from financial_brain.paper import zoo_edges as ZE


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
    con.register("ap", pd.DataFrame({"lineage": cols, "isin": [f"IN{i}" for i in range(len(cols))]}))
    con.execute("CREATE TABLE adjusted_prices AS SELECT * FROM ap")
    con.register("im", pd.DataFrame({"isin": [f"IN{i}" for i in range(len(cols))], "industry": ind,
                                     "index_name": "NIFTY 500"}))
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


def test_h005_skips_industries_with_fewer_than_eight_names():
    P, cols = _panel()
    con = _rcon(cols)
    con.execute("UPDATE index_membership SET industry = 'TINY' WHERE isin IN ('IN39', 'IN38')")
    out = ZE.h_targets(con, P)
    assert not {"L38", "L39"} & set(out["edge_h005_industry_mom"])
