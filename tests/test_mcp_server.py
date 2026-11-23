import json
import sys
import types

import pytest

from financial_brain.interfaces import mcp_server


class FakeMCPServer:
    def __init__(self, name):
        self.name = name
        self.tools = {}
        self.resources = {}

    def tool(self):
        def register(function):
            self.tools[function.__name__] = function
            return function
        return register

    def resource(self, uri):
        def register(function):
            self.resources[uri] = function
            return function
        return register

    def run(self, *, transport):
        self.transport = transport


def _install_sdk(monkeypatch, *, modern=True, fallback=True):
    package = types.ModuleType("mcp")
    package.__path__ = []
    server = types.ModuleType("mcp.server")
    server.__path__ = []
    if modern:
        server.MCPServer = FakeMCPServer
    fastmcp = types.ModuleType("mcp.server.fastmcp")
    if fallback:
        fastmcp.FastMCP = FakeMCPServer
    monkeypatch.setitem(sys.modules, "mcp", package)
    monkeypatch.setitem(sys.modules, "mcp.server", server)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp)


def test_read_only_tools_and_resource_bind_to_fixed_gateway_operations(monkeypatch):
    _install_sdk(monkeypatch)
    calls = []
    monkeypatch.setattr(mcp_server.gateway, "call", lambda name, args=None: calls.append((name, args)) or {"ok": True})
    server = mcp_server.create_server()
    assert server.name == "Financial-Brain Read-Only"
    assert set(server.tools) == {"fb_health", "fb_opportunities", "fb_brain", "fb_market_world"}
    assert server.tools["fb_health"]() == {"ok": True}
    assert server.tools["fb_opportunities"](3) == {"ok": True}
    assert server.tools["fb_brain"](2) == {"ok": True}
    assert server.tools["fb_market_world"]() == {"ok": True}
    assert server.tools["fb_market_world"]("2026-11-23") == {"ok": True}
    assert calls == [("health", None), ("opportunities", {"limit": 3}),
                     ("brain", {"limit": 2}), ("market_world", {}),
                     ("market_world", {"as_of": "2026-11-23"})]
    contract = json.loads(server.resources["financial-brain://contract"]())
    assert contract["transport"] == "stdio"
    assert contract["execution_allowed"] is False
    assert "operations" in contract


def test_legacy_fastmcp_import_fallback_registers_server(monkeypatch):
    _install_sdk(monkeypatch, modern=False, fallback=True)
    assert isinstance(mcp_server.create_server(), FakeMCPServer)


def test_missing_optional_sdk_fails_with_install_instruction(monkeypatch):
    _install_sdk(monkeypatch, modern=False, fallback=False)
    with pytest.raises(RuntimeError, match=r"install the optional MCP extra: pip install -e '\.\[mcp\]'|install the optional MCP extra"):
        mcp_server.create_server()


def test_main_starts_only_stdio_transport(monkeypatch):
    _install_sdk(monkeypatch)
    instance = FakeMCPServer("Financial-Brain Read-Only")
    monkeypatch.setattr(mcp_server, "create_server", lambda: instance)
    mcp_server.main()
    assert instance.transport == "stdio"
