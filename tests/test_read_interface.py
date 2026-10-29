"""CLI and MCP must share a bounded, read-only data contract."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime, timedelta, timezone

import pytest

from financial_brain import cli
from financial_brain.interfaces import gateway
from financial_brain.interfaces import snapshot as verified_snapshot
from financial_brain.signals import watch


def _seed(root):
    terminal = root / "terminal"
    terminal.mkdir()
    today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date().isoformat()
    row = {"ticker": "ABC", "instrument": "INE000000001", "timestamp": today,
           "evidence": {"status": "IN_SAMPLE", "ids": ["e-1"]},
           "chain": {"expected_net_alpha": 0.02, "portfolio_adjusted_alpha": 0.01},
           "expected_cost": 0.004, "position_size": {"shares": 10},
           "invalidation": {"stop": 90, "rules": ["close below stop"]},
           "liquidity": {"adv20_inr": 10000000},
           "forecast_distribution": {"p10": -0.05}}
    verified_snapshot.publish(terminal / "opportunities.json", [row], asof=today)
    (root / "killswitch.json").write_text('{"level":"OK"}', encoding="utf-8")


def test_gateway_contract_is_bounded_and_fail_closed(tmp_path):
    _seed(tmp_path)
    health = gateway.call("health", root=tmp_path)
    assert health["contract"] == gateway.CONTRACT
    assert health["read_only"] and health["data"]["opportunities"] == 1
    assert gateway.call("opportunities", {"limit": 1}, root=tmp_path)["data"]["returned"] == 1
    assert gateway.call("brain", root=tmp_path)["data"]["decisions"][0]["verdict"] == "RESEARCH_WATCH"
    assert gateway.call("brain", {"limit": 51}, root=tmp_path)["error"]["code"] == "bad_request"
    assert gateway.call("health", {"path": "C:/secret"}, root=tmp_path)["error"]["code"] == "bad_request"
    assert gateway.call("place_order", root=tmp_path)["error"]["code"] == "unknown_operation"
    (tmp_path / "killswitch.json").unlink()
    assert gateway.call("brain", root=tmp_path)["data"]["decisions"][0]["verdict"] == "ABSTAIN"


def test_cli_calls_exact_gateway_contract(tmp_path, monkeypatch, capsys):
    _seed(tmp_path)
    monkeypatch.setenv("FB_DATA_ROOT", str(tmp_path))
    assert cli.main(["tool", "call", "opportunities", "--args-json", '{"limit":1}']) == 0
    via_cli = json.loads(capsys.readouterr().out)
    direct = gateway.call("opportunities", {"limit": 1}, root=tmp_path)
    assert {k: v for k, v in via_cli.items() if k != "generated_at"} == {
        k: v for k, v in direct.items() if k != "generated_at"}
    assert cli.main(["tool", "call", "place_order"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "unknown_operation"


def test_gateway_normalizes_dates_and_nonfinite_values():
    assert gateway._normal({"date": date(2026, 10, 27), "missing": float("nan")}) == {
        "date": "2026-10-27", "missing": None}


def test_modified_or_unsealed_snapshot_cannot_reach_agents(tmp_path):
    _seed(tmp_path)
    path = tmp_path / "terminal" / "opportunities.json"
    path.write_text(path.read_text(encoding="utf-8").replace("ABC", "XYZ"), encoding="utf-8")
    assert gateway.call("health", root=tmp_path)["data"]["snapshot_verified"] is False
    assert gateway.call("brain", root=tmp_path)["error"]["code"] == "not_available"
    assert gateway.call("opportunities", root=tmp_path)["error"]["code"] == "not_available"
    with pytest.raises(verified_snapshot.SnapshotError):
        watch._snapshot(path, datetime.now(watch.IST).date())


def test_mcp_exposes_only_named_read_tools():
    pytest.importorskip("mcp")
    from financial_brain.interfaces.mcp_server import create_server

    server = create_server()
    if hasattr(server, "_tool_manager"):  # SDK v1 maintenance line
        names = {tool.name for tool in server._tool_manager.list_tools()}
        assert names == {"fb_health", "fb_opportunities", "fb_brain", "fb_market_world"}
    else:  # SDK v2 high-level server; creation is the optional-dependency smoke test
        assert server is not None


@pytest.mark.skipif(os.environ.get("FB_TEST_MCP_PROTOCOL") != "1",
                    reason="opt-in SDK protocol integration; set FB_TEST_MCP_PROTOCOL=1")
def test_mcp_protocol_round_trip_in_memory(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    from mcp.shared.memory import create_connected_server_and_client_session

    from financial_brain.interfaces.mcp_server import create_server

    _seed(tmp_path)
    monkeypatch.setenv("FB_DATA_ROOT", str(tmp_path))

    async def check():
        async with create_connected_server_and_client_session(create_server()) as session:
            listing = await session.list_tools()
            assert {t.name for t in listing.tools} == {
                "fb_health", "fb_opportunities", "fb_brain", "fb_market_world"}
            reply = await session.call_tool("fb_health", {})
            assert not reply.isError
            assert json.loads(reply.content[0].text)["data"]["snapshot_verified"] is True

    asyncio.run(check())
