"""Local stdio MCP facade over the versioned read-only gateway.

Only named, bounded research tools are registered. Never register `fb live`, order,
approval, ingestion, arbitrary SQL, arbitrary path or shell operations here.
"""
from __future__ import annotations

import json

from . import gateway


def create_server():
    try:
        from mcp.server import MCPServer  # current SDK v2
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as MCPServer  # v1 maintenance line
        except ImportError as exc:
            raise RuntimeError("install the optional MCP extra: pip install -e '.[mcp]'") from exc

    server = MCPServer("Financial-Brain Read-Only")

    @server.tool()
    def fb_health() -> dict:
        """Inspect saved research artifacts and kill-switch presence; never authorise trading."""
        return gateway.call("health")

    @server.tool()
    def fb_opportunities(limit: int = 10) -> dict:
        """List at most 50 saved research opportunities with their original evidence labels."""
        return gateway.call("opportunities", {"limit": limit})

    @server.tool()
    def fb_brain(limit: int = 10) -> dict:
        """Run read-only specialist review; verdicts are not buy or sell instructions."""
        return gateway.call("brain", {"limit": limit})

    @server.tool()
    def fb_market_world(as_of: str = "") -> dict:
        """Read the archived market world state up to an ISO date; no live feed or orders."""
        return gateway.call("market_world", {"as_of": as_of} if as_of else {})

    @server.resource("financial-brain://contract")
    def contract() -> str:
        """Machine-readable catalogue of the local read-only operations."""
        return json.dumps({"contract": gateway.CONTRACT, "operations": gateway.OPERATIONS,
                           "transport": "stdio", "execution_allowed": False}, sort_keys=True)

    return server


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
