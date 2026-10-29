# Financial-Brain read-only MCP and CLI contract

`financial-brain.read.v1` is one JSON contract, implemented in
`src/financial_brain/interfaces/gateway.py`. The CLI and MCP tools call the same gateway;
neither shells out to the other. Every response includes `contract`, `operation`,
`generated_at`, `read_only`, `ok`, and either `data` or a machine-readable `error`.
Dates are ISO strings and nonfinite numbers become JSON null. `generated_at` is the time
the response was assembled, **not** the age of the underlying market data.
The workstation publishes `opportunities.json` and a manifest containing its source
date, row count and SHA-256. The manifest is published last; mixed or modified pairs are
rejected by the gateway and market watcher. Regenerate the terminal once after upgrading
to create the manifest. A local digest is an integrity check, not a trusted timestamp.

| Operation | Arguments | Source | Limit |
|---|---|---|---|
| `health` | none | verified snapshot, kill-switch and DB presence | status only |
| `opportunities` | `limit` | saved workstation snapshot | 1–50 rows |
| `brain` | `limit` | saved snapshot and persisted kill switch | 1–50 read-only verdicts |
| `market_world` | `as_of` (ISO date, optional) | research DB, opened read-only | one world state |

These operations cannot accept arbitrary paths, SQL, order parameters, approval actors,
or network destinations. `FB_DATA_ROOT` is set by the local operator at process launch;
it cannot be changed through a tool call. A missing kill-switch file causes the brain
to abstain. The `health` response does not certify freshness or an edge.

## CLI

Install the project with `pip install -e .`, then:

```text
fb tool list
fb tool call health
fb tool call opportunities --args-json '{"limit":5}'
fb tool call brain --args-json '{"limit":5}'
fb tool call market_world --args-json '{"as_of":"2026-10-27"}'
```

The CLI exits 0 for a successful response and 2 for a contract/data error. Existing
research and operational `fb` commands remain available, but **only** the four `fb tool`
operations are exposed through the MCP server.

## MCP stdio

Install the optional SDK with `pip install -e ".[mcp]"`, then configure a local MCP
host to launch `python -m financial_brain.interfaces.mcp_server` with the repository as
its working directory and, if needed, `FB_DATA_ROOT` pointing to the permitted local
data directory. It registers `fb_health`, `fb_opportunities`, `fb_brain`, and
`fb_market_world`, plus the `financial-brain://contract` resource. The server uses
**stdio only**; do not expose it as an unauthenticated HTTP service. Tool output can
contain private research data, so give access only to trusted local hosts.

The server supports the installed MCP SDK v1 maintenance line and the v2 import path.
The current official SDK documents v2 as the stable line; test against that SDK in a
clean environment before claiming v2 production compatibility. The common protocol
contract is independent of the SDK version.

No tool can approve a trade, reset a kill switch, send an alert, call Kite, place an
order, or edit a database. The agent coordinator is a research reviewer, not an
autonomous trading authority.
