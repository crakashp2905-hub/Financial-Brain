"""The owner's account (read only), the journal and its autopsy, and alerts that fire once."""
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from financial_brain import alerts as A
from financial_brain.portfolio import journal as J
from financial_brain.portfolio import my_book as MB
from financial_brain.providers import kite_account as KA


# --- the account module can only read three things ---------------------------------------------------------------

def test_only_three_account_paths_exist_and_they_match_exactly():
    assert KA.ACCOUNT_ENDPOINTS == {"/portfolio/holdings", "/portfolio/positions", "/trades"}
    for bad in ("/portfolio/positions/convert", "/trades/x", "/portfolio", "/user/margins"):
        with pytest.raises(KA.KiteAccountError, match="refusing"):
            KA._get(bad)


def test_the_account_module_has_no_write_verb_or_order_path():
    body = Path(KA.__file__).read_text(encoding="utf-8")
    for bad in ('method="POST"', 'method="PUT"', 'method="DELETE"', "/orders", "/gtt"):
        assert bad not in body
    assert 'method="GET"' in body


# --- the book -----------------------------------------------------------------------------------------------------

def test_sync_stores_a_day_and_is_idempotent(tmp_path):
    con = MB.connect(tmp_path / "p.duckdb")
    h = [{"tradingsymbol": "ABC", "exchange": "NSE", "isin": "INE1", "quantity": 10, "t1_quantity": 0,
          "average_price": 100.0, "last_price": 110.0, "close_price": 108.0, "pnl": 100.0, "day_change_percentage": 1.8}]
    t = [{"trade_id": "1", "tradingsymbol": "ABC", "exchange": "NSE", "product": "CNC", "transaction_type": "BUY",
          "quantity": 10, "average_price": 100.0, "fill_timestamp": "2026-10-06 10:00:00"}]
    for _ in range(2):
        MB.sync(con, date(2026, 10, 6), holdings_fn=lambda: h, positions_fn=lambda: {"net": []}, trades_fn=lambda: t)
    b = MB.latest(con)
    assert len(b) == 1 and b["value"].iloc[0] == 1100 and b["since_cost"].iloc[0] == pytest.approx(0.10)
    assert con.execute("SELECT COUNT(*) FROM my_trades").fetchone()[0] == 1


def test_thesis_health_exits_by_rule_below_the_stop_or_in_the_bottom_fifth():
    f = pd.Series({"above_ma200": True, "mom_12_1": 0.2, "atr_pctile_250": 0.5})
    ok = MB.thesis_health(f, avg_price=100, last_price=105, stop_pct=0.1, composite_pct=0.7)
    assert ok["recommendation"] == MB.HOLD and ok["score"] == 100
    assert MB.thesis_health(f, avg_price=100, last_price=89, stop_pct=0.1, composite_pct=0.7)["recommendation"] == MB.EXIT
    assert MB.thesis_health(f, avg_price=100, last_price=105, stop_pct=0.1, composite_pct=0.1)["recommendation"] == MB.EXIT
    weak = pd.Series({"above_ma200": False, "mom_12_1": -0.2, "atr_pctile_250": 0.95})
    low = MB.thesis_health(weak, avg_price=100, last_price=103, stop_pct=0.1, composite_pct=0.45)
    assert low["score"] < 35 and low["recommendation"] == MB.REVIEW       # a low score alone never exits


# --- the journal --------------------------------------------------------------------------------------------------

def trades(rows):
    return pd.DataFrame(rows, columns=["trade_id", "ts", "day", "tradingsymbol", "product", "side", "quantity", "price"])


def test_round_trips_match_fifo_including_partial_fills_and_shorts():
    t = trades([("1", "09:20", "d1", "A", "CNC", "buy", 10, 100.0), ("2", "09:30", "d1", "A", "CNC", "buy", 10, 110.0),
                ("3", "10:00", "d2", "A", "CNC", "sell", 15, 120.0),
                ("4", "09:40", "d1", "B", "MIS", "sell", 5, 50.0), ("5", "15:00", "d1", "B", "MIS", "buy", 5, 48.0)])
    rt = J.round_trips(t).set_index(["symbol", "entry_px"])
    assert rt.loc[("A", 100.0), "qty"] == 10 and rt.loc[("A", 110.0), "qty"] == 5
    assert rt.loc[("B", 50.0), "side"] == "short" and rt.loc[("B", 50.0), "gross"] == pytest.approx(10.0)


def test_the_autopsy_adds_up_to_the_net_return():
    trip = {"side": "long", "entry_px": 100.0, "exit_px": 110.0, "ret": 0.10}
    stock = 108.0 / 101.0 - 1                                # close to close over the trip
    a = J.autopsy(trip, stock_ret=stock, market_ret=0.03, sector_ret=0.05, beta=1.0, sector_beta=0.5,
                  entry_close=101.0, exit_close=108.0, cost=0.003)
    assert a["market"] == pytest.approx(0.03) and a["sector"] == pytest.approx(0.01)
    assert a["selection"] == pytest.approx(stock - 0.04)
    assert abs(a["residual"]) < 0.003                        # only the cross-terms of compounding remain


def test_the_report_waits_for_enough_trips_before_naming_a_weakness():
    small = pd.DataFrame({"net": [0.01] * 5, "selection": 0.01, "entry": -0.002, "exit": 0.0,
                          "entry_day": "2026-10-01", "exit_day": "2026-10-03"})
    assert J.report(small)["judged"] is False and "weakness" not in J.report(small)
    big = pd.concat([small] * 7, ignore_index=True)
    assert J.report(big)["weakness"] == "entry"


# --- alerts -------------------------------------------------------------------------------------------------------

def test_an_alert_is_sent_once(tmp_path):
    sent = []
    a = A.system(day="2026-10-06", killswitch_level="OK", run_status="killed")
    assert a and "did not complete" in a[0]["title"]
    for _ in range(2):
        A.send(a, notify=lambda t, b: sent.append(t) or True, path=tmp_path / "s.json")
    assert len(sent) == 1


def test_only_an_exit_by_rule_holding_alerts():
    rows = [{"symbol": "A", "recommendation": "HOLD", "score": 80, "reason": "-"},
            {"symbol": "B", "recommendation": "EXIT BY RULE", "score": 20, "reason": "below the stop"}]
    assert [x["key"] for x in A.holdings(rows, "d")] == ["thesis:d:B"]
