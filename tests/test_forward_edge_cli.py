import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import duckdb

from financial_brain.forward_edge import cli
from financial_brain.forward_edge.session_runner import ForwardSessionRunner
from financial_brain.forward_edge.session_state import ForwardRunConfig, ForwardSessionStore


ROOT = Path(__file__).resolve().parents[1]


def test_fixture_pack_is_deterministic_and_costs_are_net_of_pnl():
    pack = json.loads((ROOT / "docs/forward-edge/fixtures/session-cases.v1.json").read_text())
    assert pack["schema"] == "fb.forward-edge.session-fixtures.v1"
    cases = {case["id"]: case for case in pack["cases"]}
    assert set(cases) == {"win", "loss", "no_trade", "rejection", "late_quote", "unknown_order", "adverse_fill"}
    for name in ("win", "loss", "adverse_fill"):
        case = cases[name]
        net = (case["exit_price"] - case["entry_price"]) - case["costs"]
        assert net == case["expected_net_pnl"]
    assert cases["late_quote"]["quote_age_ms"] > cases["late_quote"]["maximum_age_ms"]
    assert cases["unknown_order"]["new_entries_allowed"] is False


def test_replay_run_defaults_to_non_mutating_dry_run(capsys):
    args = type("Args", (), {"date": "2026-11-13", "execute": False, "names": 200})()
    assert cli._paper_run(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] is True
    assert result["broker_orders"] is False
    assert result["mode"] == "RESEARCH_REPLAY"


def test_preflight_never_calls_readiness_green_a_forward_session(capsys):
    args = type("Args", (), {"repo": str(ROOT), "json": True})()
    cli._preflight(args)
    result = json.loads(capsys.readouterr().out)
    assert result["paper_session_ready"] is False
    assert result["paper_session_blockers"]


def test_close_check_is_read_only_and_never_upgrades_replay(tmp_path, capsys):
    ledger = tmp_path / "paper.duckdb"
    with duckdb.connect(str(ledger)) as con:
        con.execute("CREATE TABLE runs (day VARCHAR, mode VARCHAR, status VARCHAR, finished VARCHAR)")
        con.execute("INSERT INTO runs VALUES ('2026-11-13', 'replay', 'complete', '2026-11-13T10:00:00Z')")
    args = type("Args", (), {"date": "2026-11-13", "ledger": str(ledger)})()
    assert cli._paper_close(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["status"] == "REPLAY_CLOSED"
    assert first["verified_forward_evidence"] is False
    assert cli._paper_close(args) == 0
    second = json.loads(capsys.readouterr().out)
    assert second == first


def test_session_resume_cli_rehydrates_an_existing_session(tmp_path, capsys):
    state_path = tmp_path / "state.sqlite"
    journal_path = tmp_path / "journal.sqlite"
    config = ForwardRunConfig(
        date(2026, 11, 13), "cohort-v1", "a" * 64, "NIFTY50_TRI", ("NSE:INFY",),
        Decimal("100000"), {"max_positions": 1}, "ledger-1")
    runner = ForwardSessionRunner(ForwardSessionStore(state_path), journal_path)
    runner.begin("cli-resume", config, now=datetime(2026, 11, 13, 4, 45, tzinfo=timezone.utc),
                 preflight={"risk_policy_loaded": True})
    runner.close()

    args = SimpleNamespace(resume="cli-resume", state_db=str(state_path), journal=str(journal_path))
    assert cli._resume_session(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["session_id"] == "cli-resume"
    assert result["state"] == "OPEN"
    assert result["physical_orders_enabled"] is False
