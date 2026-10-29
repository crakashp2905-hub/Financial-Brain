from datetime import date

import duckdb
import pandas as pd

from financial_brain.paper import book as B


def test_swing_book_records_cost_dominated_signal_as_skip(monkeypatch):
    con = duckdb.connect()
    con.execute(B.SCHEMA)
    con.execute("ALTER TABLE positions ADD COLUMN why VARCHAR")
    ranked = pd.DataFrame([{"ticker": "X", "lineage": "LX", "type": "swing", "horizon": 7,
                            "price": 100.0, "invalidation": 90.0, "shares": 10,
                            "expected_return": 0.001, "expected_net": -0.009, "cost": 0.01}])
    monkeypatch.setattr(B, "swing_ranking", lambda *a, **kw: ranked)
    assert B.step(None, con, date(2026, 10, 29), mode="replay", spec=B.SWING) == 0
    assert con.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
    assert "round-trip costs" in con.execute("SELECT reason FROM skips").fetchone()[0]
