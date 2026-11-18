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


def _bar(hh, mm, o, h, lo, c):
    return (datetime(2026, 11, 18, hh, mm, tzinfo=lb.IST), o, h, lo, c)


def test_unexited_tags_run_status():
    t = {**TR, "exit_ts": None, "exit_px": None, "day": "2026-11-03"}
    u = lb.unexited([TR | {"day": "2026-11-18"}, t], [{"day": "2026-11-03", "status": "aborted", "note": "dns"}])
    assert len(u) == 1 and u[0]["run_status"] == "aborted"


def test_reconstruct_adverse_first_gap_and_flatten():
    t = {**TR, "entry_ts": "2026-11-18T09:32:00"}          # short, stop 103, target 94
    assert lb.reconstruct_exit(t, [_bar(10, 0, 100, 104, 93, 99)])["reason"] == "stop"
    assert lb.reconstruct_exit(t, [_bar(10, 0, 105, 106, 104, 105)])["px"] == 105
    r = lb.reconstruct_exit(t, [_bar(10, 0, 100, 101, 99, 100), _bar(15, 15, 98, 99, 97, 97.5)])
    assert r == {"reason": "time", "px": 97.5, "at": _bar(15, 15, 0, 0, 0, 0)[0], "basis": "RECONSTRUCTED"}
    assert lb.reconstruct_exit(t, [])["reason"] == "UNPRICED"
    assert lb.gross(t, 98.0) == 20.0


def test_reconstruct_last_bar_when_session_data_ends_early():
    t = {**TR, "entry_ts": "2026-11-18T09:32:00"}
    r = lb.reconstruct_exit(t, [_bar(10, 0, 100, 101, 99, 100), _bar(15, 14, 99, 99.5, 98.5, 99)])
    assert r["reason"] == "time" and r["px"] == 99 and r["basis"] == "RECONSTRUCTED_LAST_BAR"
