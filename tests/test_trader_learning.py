"""The owner's learning loop: broker-export import, behaviour detectors and the anti-playbook."""
import numpy as np
import pandas as pd
import pytest

from financial_brain.portfolio import behaviour_review as BR
from financial_brain.portfolio import journal as J
from financial_brain.portfolio import my_book as MB
from financial_brain.portfolio import tradebook_import as TI

CSV = """symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,trade_id,order_id,order_execution_time
INFY,INE009A01021,2026-09-01,NSE,EQ,EQ,buy,false,10,1500,T1,O1,2026-09-01T09:20:00
INFY,INE009A01021,2026-09-03,NSE,EQ,EQ,buy,false,10,1400,T2,O2,2026-09-03T10:00:00
INFY,INE009A01021,2026-09-10,NSE,EQ,EQ,sell,false,20,1480,T3,O3,2026-09-10T11:00:00
"""


def _trades():
    return pd.DataFrame(TI.parse(CSV))


def test_import_is_idempotent_and_keeps_the_journal_shape(tmp_path):
    con = MB.connect(tmp_path / "b.duckdb")
    assert TI.load(con, TI.parse(CSV)) == {"rows": 3, "added": 3, "already_present": 0}
    assert TI.load(con, TI.parse(CSV))["added"] == 0
    t = con.execute("SELECT trade_id, ts, day, tradingsymbol, product, side, quantity, price FROM my_trades").df()
    trips = J.round_trips(t)
    assert len(trips) == 2 and set(trips["product"]) == {"CNC"}


def test_a_file_that_is_not_a_tradebook_is_refused():
    with pytest.raises(TI.ImportError_):
        TI.parse("a,b\n1,2\n")


def test_averaging_down_is_flagged_once():
    ad = BR.averaging_down(_trades())
    assert len(ad) == 1 and ad[0]["day"] == "2026-09-03" and ad[0]["avg_cost"] == 1500


def _closes(path):
    idx = pd.bdate_range("2026-05-01", periods=len(path))
    return pd.Series(path, index=idx)


def test_detectors_on_a_constructed_path():
    flat = list(np.full(80, 100.0) * (1 + 0.01 * np.sin(np.arange(80))))       # ~1% daily moves
    jump = flat + [107.0]                                                        # +7% the day before entry
    after = [107.0, 90.0, 89.0, 88.0, 87.0, 86.0, 95.0]                          # far below the stop, held on
    path = jump + after + [100.0] * 25
    c = _closes(path)
    entry_day, exit_day = c.index[81], c.index[87]
    trip = pd.DataFrame([{"symbol": "X", "side": "long", "entry_day": entry_day, "exit_day": exit_day,
                          "entry_px": 107.0, "exit_px": 95.0, "ret": 95 / 107 - 1}])
    tags = BR.tag_trips(trip, lambda s: c).iloc[0]["tags"]
    assert "event_chasing" in tags and "ignored_invalidation" in tags and "premature_exit" not in tags
    win = trip.assign(exit_px=90.0, ret=0.01, exit_day=c.index[82], entry_px=89.0)
    assert "premature_exit" in BR.tag_trips(win, lambda s: c).iloc[0]["tags"]       # it ran on to 100+


def test_anti_playbook_needs_size_sign_and_t():
    rows = [{"tags": ["event_chasing"], "net": -0.02 + 0.002 * (i % 3)} for i in range(10)]
    rows += [{"tags": ["premature_exit"], "net": 0.01} for _ in range(10)]
    rows += [{"tags": ["overtrading"], "net": -0.05} for _ in range(3)]          # too few
    ap = BR.anti_playbook(pd.DataFrame(rows))
    assert [e["tag"] for e in ap["entries"]] == ["event_chasing"] and ap["examined"] == 3


def test_overtrading_days():
    t = pd.DataFrame({"day": ["d1"] * 11 + ["d2"] * 3})
    assert BR.overtrading_days(t) == ["d1"]
