# Frozen session coverage CLI

`python -m financial_brain.forward_edge.cli session-coverage` renders the planned
session denominator from the registered immutable schedule and append-only session
date ledger. The caller supplies the versioned exchange calendar and an explicit
report clock so the output can be reproduced later.

```powershell
python -m financial_brain.forward_edge.cli session-coverage `
  --registry data/paper/frozen-schedules.jsonl `
  --schedule-id cash-cohort-2026q4 `
  --ledger data/paper/session-dates.jsonl `
  --calendar data/calendars/nse.json `
  --window-start 2026-11-16 `
  --window-end 2026-11-20 `
  --as-of 2026-11-20T12:00:00+00:00
```

The command is read-only and emits the hashed JSON and human-readable report to
stdout. Exit code `0` means the window has no pending or unqualified scheduled
outcomes and no omitted tradable dates; exit code `3` means coverage is incomplete.
It does not create a schedule, infer missing sessions, or establish that a chosen
cohort includes every eligible opportunity. Cohort approval and prospective market
operation remain separate evidence requirements.
