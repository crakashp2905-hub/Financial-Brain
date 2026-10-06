"""Learning from bad trades: context recorded at entry, losing situations found, nothing applied automatically."""
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from financial_brain.paper import intraday as I
from financial_brain.paper import loss_review as L


def test_the_engine_records_the_market_move_known_at_entry():
    e = I.DayEngine("2026-10-06")
    e.daily_up = False
    for sym, prev in (("A", 100.0), ("B", 100.0)):
        e.add_symbol(sym, prev)
        e.set_opening(sym, open_px=100.0, or_hi=101.0, or_lo=99.0)
    e.on_price("B", datetime(2026, 10, 6, 9, 31), 98.0)       # the market (B) is falling
    t = e.on_price("A", datetime(2026, 10, 6, 9, 32), 101.5)   # A breaks out upward
    trade = next(x for x in t if x.strategy == "ORB")
    assert trade.side == "long" and trade.daily_up is False and trade.entry_minute == 9 * 60 + 32
    assert trade.mkt_ret == pytest.approx(((101.5 / 100 - 1) + (98 / 100 - 1)) / 2)
    assert trade.or_width == pytest.approx(0.02) and trade.gap == pytest.approx(0.0)


def trades(n_days=10, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(n_days):
        for k in range(20):
            side = "long" if k % 2 else "short"
            mkt = 0.004 if d % 2 else -0.004
            against = (mkt > 0) != (side == "long")
            bp = (-40 if against else 5) + rng.normal(0, 5)
            rows.append({"day": f"2026-09-{d + 1:02d}", "mode": "replay", "strategy": "ORB", "symbol": f"S{k}",
                         "side": side, "notional": 1e5, "net_pnl": bp * 10, "exit_reason": "time", "mkt_ret": mkt,
                         "daily_up": True, "gap": 0.001, "or_width": 0.01, "entry_minute": 580})
    return L.label(pd.DataFrame(rows))


def test_trades_against_the_market_are_found_as_a_losing_pattern():
    r = L.review(trades())
    pats = set(r["losing"]["pattern"])
    assert "vs_market_today = against the market" in pats
    assert "vs_market_today = with the market" not in pats
    assert r["groups_examined"] >= 5 and "by chance" in r["chance_note"]


def test_too_few_days_judges_nothing():
    r = L.review(trades(n_days=3))
    assert r["losing"].empty
    assert "none is judged" in L.render(r)


def test_a_hypothesis_is_proposed_never_promoted(tmp_path):
    con = duckdb.connect(str(tmp_path / "h.duckdb"))
    r = L.review(trades())
    assert L.propose(con, r) >= 1
    con.execute("UPDATE hypotheses SET status = 'PRE-REGISTERED'")
    L.propose(con, r)                                            # a later review keeps the human-set status
    assert {s for (s,) in con.execute("SELECT DISTINCT status FROM hypotheses").fetchall()} == {"PRE-REGISTERED"}


def test_the_review_has_no_way_to_change_a_rule():
    src = Path(L.__file__).read_text(encoding="utf-8")
    for bad in ("VERSION =", "TARGET_R", "place_order", "STRATEGIES"):
        assert bad not in src


def test_the_with_market_view_splits_the_real_trades(tmp_path):
    from financial_brain.paper import intraday_runner as R
    con = R.ledger(tmp_path / "l.duckdb")
    e = I.DayEngine("2026-10-06")
    for sym in ("A", "B", "C"):
        e.add_symbol(sym, 100.0)
        e.set_opening(sym, open_px=100.0, or_hi=101.0, or_lo=99.0)
    e.on_price("C", datetime(2026, 10, 6, 9, 31), 97.0)                 # market falling
    for t in e.on_price("A", datetime(2026, 10, 6, 9, 32), 101.5):      # long: against the market
        R.save(con, t, "replay")
    for t in e.on_price("B", datetime(2026, 10, 6, 9, 33), 98.5):       # short: with the market
        R.save(con, t, "replay")
    for t in e.end_of_day(datetime(2026, 10, 6, 15, 20), {"A": 100.0, "B": 98.0, "C": 97.0}):
        R.save(con, t, "replay")
    v = L.with_market_view(con)
    # C's own short breakout (09:31, market falling) is with the market too
    assert v["with"]["trades"] == 2 and v["against"]["trades"] == 1 and v["status"].startswith("SHADOW")
