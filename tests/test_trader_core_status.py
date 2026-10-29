from datetime import date

import duckdb

from financial_brain.interfaces import snapshot, trader_core


def test_missing_snapshot_abstains(tmp_path):
    result = trader_core.status(tmp_path, today=date(2026, 10, 29))
    assert result["status"] == "ABSTAIN"
    assert result["execution_allowed"] is False


def test_valid_snapshot_without_kill_state_is_research_only(tmp_path):
    path = tmp_path / "terminal" / "opportunities.json"
    snapshot.publish(path, [{"timestamp": "2026-10-29", "ticker": "TEST",
                             "instrument": "INE000000001"}], asof="2026-10-29")
    result = trader_core.status(tmp_path, today=date(2026, 10, 29))
    assert result["status"] == "RESEARCH_ONLY"
    assert result["by_verdict"]["ABSTAIN"] == 1
    assert result["kill_switch"] == "UNKNOWN"
    assert result["champion_evidence"]["status"] == "LEDGER_UNAVAILABLE"
    assert result["holdings"]["status"] == "UNAVAILABLE"
    assert "kill-switch state missing or invalid" in result["evidence_gaps"]
    assert result["execution_allowed"] is False


def test_tampered_snapshot_abstains(tmp_path):
    path = tmp_path / "terminal" / "opportunities.json"
    snapshot.publish(path, [{"timestamp": "2026-10-29"}], asof="2026-10-29")
    path.write_text("[]", encoding="utf-8")
    assert trader_core.status(tmp_path, today=date(2026, 10, 29))["status"] == "ABSTAIN"


def test_saved_holdings_are_labelled_not_live(tmp_path):
    path = tmp_path / "terminal" / "opportunities.json"
    snapshot.publish(path, [], asof="2026-10-29")
    with duckdb.connect(str(tmp_path / "my_portfolio.duckdb")) as con:
        con.execute("CREATE TABLE holdings_snap (day DATE, tradingsymbol VARCHAR, quantity DOUBLE, last_price DOUBLE, pnl DOUBLE)")
        con.execute("INSERT INTO holdings_snap VALUES ('2026-10-28', 'TEST', 3, 100, 12)")
    result = trader_core.status(tmp_path, today=date(2026, 10, 29))
    assert result["holdings"]["status"] == "CURRENT_SNAPSHOT"
    assert result["holdings"]["positions"][0]["symbol"] == "TEST"
    assert "not a live" in result["holdings"]["note"]
