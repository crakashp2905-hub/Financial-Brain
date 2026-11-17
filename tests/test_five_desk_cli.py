from __future__ import annotations

import json
from datetime import date

from financial_brain.paper.five_desk_cli import (
    desks_status,
    heatmap,
    main,
    parse_portfolio_disclosure,
    pnl_by_reason,
    weekly_mf_review_due,
)


def test_desks_status_shows_missing_desks():
    out = desks_status([{"desk": "ETF", "status": "HALTED"}])
    assert out[3].endswith("HALTED") and out[0].endswith("NO_DATA")


def test_signals_with_exits_cli(tmp_path, capsys):
    f = tmp_path / "s.json"
    f.write_text(json.dumps([{"desk": "SWING", "instrument_id": "IN:X", "side": "BUY", "intent": "EXCHANGE_LIMIT",
                              "stop_price": "97", "target_price": "106", "close_action": "SELL_TO_CLOSE"}]))
    assert main(["signals", "--file", str(f), "--with-exits"]) == 0
    assert "SL 97 TP 106 close SELL_TO_CLOSE" in capsys.readouterr().out


def test_exits_and_pnl_by_reason(tmp_path, capsys):
    rows = [{"desk": "SWING", "position_id": "P1", "reason": "TP", "fill_price": "106", "pnl": 60},
            {"desk": "ETF", "position_id": "P2", "reason": "STOP", "pnl": None}]
    f = tmp_path / "e.json"
    f.write_text(json.dumps(rows))
    main(["exits", "--file", str(f), "--desk", "etf"])
    assert "UNRESOLVED" in capsys.readouterr().out
    assert pnl_by_reason(rows) == {"TP": 60.0}


def test_heatmap():
    out = heatmap([{"index_id": "NIFTY", "probs": {"UP": 0.5, "DOWN": 0.3, "FLAT": 0.2},
                    "model_version": "1", "optionable": True, "at": "09:30"}])
    assert out[0].startswith("NIFTY        UP    0.50 v1 OPTIONS")


def test_weekly_review():
    assert weekly_mf_review_due(date(2026, 11, 17), None)
    assert not weekly_mf_review_due(date(2026, 11, 17), date(2026, 11, 12))


def test_portfolio_disclosure_parser():
    rows, bad = parse_portfolio_disclosure("name,isin,sector,weight\nA,INE1,IT,60\nB,INE2,,40\n")
    assert len(rows) == 2 and rows[1]["sector"] == "UNKNOWN" and bad == []
    rows, bad = parse_portfolio_disclosure("name,isin,sector,weight\nA,INE1,IT,x\nB,INE2,,40\n")
    assert len(bad) == 2
