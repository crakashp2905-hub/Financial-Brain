"""An opportunity's whole life read back from what each step recorded: found, rejected or taken, sold, resolved."""
from datetime import date, timedelta

import pandas as pd

from financial_brain.opportunity import lifecycle as LC
from financial_brain.paper import book as B


def test_a_book_records_why_it_did_not_take_a_top_name(tmp_path, monkeypatch):
    con = B.connect(tmp_path / "b.duckdb")
    d0 = date(2026, 10, 1)
    con.execute("""INSERT INTO positions (version, mode, signal_day, rank, ticker, lineage, type, horizon, shares, stop,
                   ref_price, expected_net, cost) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [B.POSITION.version, "replay", d0 - timedelta(days=1), 1, "A", "LA", "x", 60, 1, 1.0, 1.0, 0.0, 0.0])
    ranked = pd.DataFrame({"ticker": list("ABCDEFG"), "lineage": [f"L{x}" for x in "ABCDEFG"], "type": "x", "horizon": 60,
                           "price": 100.0, "invalidation": 90.0, "expected_net": [0.01] * 7, "adv20": 1e9, "vol_60": 0.3})
    monkeypatch.setattr(B, "ranking", lambda rcon, day, capital=B.CAPITAL: ranked)
    monkeypatch.setattr(B, "_plan_shares", lambda r, capital, spec=B.POSITION: 10)
    B.step(None, con, d0, mode="replay", spec=B.POSITION)
    skips = dict(con.execute("SELECT ticker, reason FROM skips").fetchall())
    assert skips == {"A": "already held"}
    ev = LC.timeline("B", str(d0), book=con)
    assert [e["state"] for e in ev] == ["TAKEN"]
    ev = LC.timeline("A", str(d0), book=con)
    assert ev[0]["state"] == "REJECTED" and "already held" in ev[0]["what"]
    assert "no record" in LC.render("Z", str(d0), LC.timeline("Z", str(d0), book=con))
