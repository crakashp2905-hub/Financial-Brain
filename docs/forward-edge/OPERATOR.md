# Forward-edge operator commands

These commands do not submit broker orders. The existing intraday paper engine has both live-quote and historical
replay paths, but the unified `ForwardSessionRunner`, independently timestamped pre-entry receipts, and a single
reconciled session ledger are not yet wired. Command output must be read with that limitation in mind.

## Preflight

```powershell
fb forward preflight
fb forward preflight --json
```

Exit `0` means the repository audit checks are green. Exit `2` means formal readiness is blocked. Even a green
repository audit does not claim a session is ready; the JSON includes `paper_session_ready: false` until the
integrated runner and custody path exist.

## Paper replay

```powershell
fb forward paper-run --date 2026-11-13
fb forward paper-run --date 2026-11-13 --execute
```

The default only prints a plan. `--execute` explicitly runs the existing historical intraday replay and writes to
its paper ledger. Replay is labeled `RESEARCH_REPLAY`; it is not a forward test, does not create independent
timestamp evidence, and does not call a broker order API.

## Close check and promotion report

```powershell
fb forward close --date 2026-11-13
fb forward report --as-of 2026-11-15
```

`close` is a read-only, idempotent check of a replay row and never upgrades it to independently verified evidence.
`report` prints current audit blockers and a promotion verdict; it does not yet reconcile one forward session's
decisions, orders, fills, costs, and attribution. Synthetic acceptance cases are in
[`fixtures/session-cases.v1.json`](fixtures/session-cases.v1.json); they are test inputs, never market evidence.
