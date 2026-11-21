# Market-data permission operations

Each permission is scoped to exactly one source and one use. The supported uses are
`RESEARCH`, `PAPER_SIMULATION`, `DISPLAY`, `RETENTION`, `REDISTRIBUTION`, and
`LIVE_EXECUTION`; status is `USE_ALLOWED`, `UNKNOWN_RIGHTS`, or `DENIED`. Unknown,
missing, expired, or mismatched permissions block operational consumption.

**Kite-specific caution (reviewed 2026-11-21):** the current [Kite Connect API terms](https://www.kite.trade/terms/)
restrict live-data use in virtual/mock trading apps and permanent copies. A Zerodha-hosted
[provider clarification](https://kite.trade/forum/discussion/16304/written-clarification-for-self-built-kite-connect-trading-system)
also states that permanent data storage and paper trading using live data are restricted. A paid subscription alone
does not settle whether this private, single-user workflow is allowed. Do not mark a Kite `PAPER_SIMULATION` or
`RETENTION` permission `USE_ALLOWED` until the owner has an applicable written confirmation covering the exact
use, archive, and retention period. See `market-data-rights-review.v1.md`.

`data-permissions.v1.json` is the current register. Its 102 source/use records are
`UNKNOWN_RIGHTS` because the repository contains no reviewed provider terms that establish
those uses. A Kite API response or subscription is not itself permission evidence. Do not
change a status to `USE_ALLOWED` until the cited evidence supports that exact source/use and
the reviewer and effective dates are recorded. No API keys, session tokens, or other
credentials belong in a permission file.

For an authorized internal Kite paper-simulation run, create a separate JSON file with one
permission object, backed by the applicable account terms or written authorization:

```json
{
  "source_id": "KITE_CONNECT_READ_ONLY",
  "use": "PAPER_SIMULATION",
  "status": "USE_ALLOWED",
  "effective_from": "YYYY-MM-DD",
  "effective_through": null,
  "evidence_ref": "path or controlled reference to reviewed terms",
  "reviewer": "reviewer identity"
}
```

Then run:

```text
fb paper-intraday --data-permission-file path\to\kite-paper-permission.json
```

The permission file is an operator-supplied assertion with a traceable evidence reference;
the application does not authenticate provider contracts or replace legal review. Replay
and deterministic synthetic fixtures remain separately labeled and cannot confer rights on
observed market data.
