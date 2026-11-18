from __future__ import annotations

from datetime import date, datetime

import pytest

from financial_brain.paper import legacy_bridge as lb

TR = {"trade_id": "2026-11-18-ORB-X", "mode": "live", "version": "live-v1", "strategy": "ORB",
      "symbol": "X", "side": "short", "qty": 10, "entry_ts": "2026-11-18T09:32:57", "entry_px": 100.0,
      "stop": 103.0, "target": 94.0, "exit_ts": "2026-11-18T15:15:02", "exit_px": 98.0,
      "exit_reason": "time", "gross_pnl": 20.0}
POS = {"version": "learned-v2", "mode": "live", "signal_day": date(2026, 11, 12), "ticker": "Y",
       "lineage": "INE1", "horizon": 20, "rank": 1, "shares": 5, "stop": 90.0, "entry_day": date(2026, 11, 13),
       "entry_px": 100.0, "exit_day": None, "exit_px": None, "exit_reason": None}


def test_guard_blocks_market_hours():
    with pytest.raises(lb.BridgeError):
        lb.guard(datetime(2026, 11, 18, 10, 0, tzinfo=lb.IST))
    lb.guard(datetime(2026, 11, 18, 19, 0, tzinfo=lb.IST))
    lb.guard(datetime(2026, 11, 21, 10, 0, tzinfo=lb.IST))  # Saturday


def test_replay_is_idempotent_and_ordered():
    recs = lb.intraday_records([TR]) + lb.swing_records([POS])
    lg, n = lb.replay(recs)
    assert n == len(recs) == 9
    _, again = lb.replay(recs, lg)
    assert again == 0


def test_desk_pages():
    recs = lb.intraday_records([TR]) + lb.swing_records([POS])
    intr = lb.desk_page(recs, "INTRADAY", "live 5m")["signals"][0]
    assert intr["side"] == "SELL_SHORT" and intr["close_action"] == "BUY_TO_COVER"
    assert intr["trigger"] == "SESSION_FLATTEN" and intr["status"] == "CLOSED"
    sw = lb.desk_page(recs, "SWING", "daily close")["signals"][0]
    assert sw["status"] == "OPEN" and sw["trigger"] == "ARMED" and sw["tp"] == "HORIZON"
