# Registered paper-book daily cycle

`python -m financial_brain.forward_edge.cli daily-paper-cycle --manifest <path>` finalizes the accounts in one frozen `RegisteredPaperBooks` plan, creates each paper close archive and portable evidence bundle, then writes the by-book performance report and an immutable registered-plan portfolio reconciliation under `report_output_root/portfolio/<session-date>/<plan-id-hash>.json`. The portfolio report reopens every allocated account from its journal, validates opening capital and ledger balance, and reports cash, reserves, fees, realized P&L, and NAV availability without pooling cash. The per-account journals remain authoritative; this report is a derived view.

Run `python -m financial_brain.forward_edge.cli daily-paper-preflight --manifest <path>` first. Preflight reopens every existing account from its durable journal, validates the account against frozen allocated capital, checks every close mark and outstanding order state, verifies every dated runner session and strategy/account binding, and validates the supplied historical report rows. It does not write to account journals or close books. The cycle repeats these checks immediately before it begins.

Generate the manifest bindings with `daily-paper-manifest`. It scans one or more verified runner journals and requires exactly one terminal session for each strategy/account in the selected plan and exchange date. It does not guess among duplicate sessions.

```text
python -m financial_brain.forward_edge.cli daily-paper-manifest `
  --registry data/forward/books.jsonl --plan-id paper-plan-2026-11 `
  --account-root data/forward/accounts --runner-journal data/forward/runners.jsonl `
  --session-date <exchange-date> --captured-at <observed-close-timestamp> `
  --marks-map data/forward/close-marks-map.json --archive-dir data/forward/archives `
  --bundle-root data/forward/bundles --report-output-root data/forward/reports `
  --output data/forward/daily-cycle.json
```

Use either `--marks-map` or `--derive-kite-marks --max-mark-age-seconds <limit>`. The marks map is a JSON object keyed by every registered strategy ID; each value is the path to that book's owner-observed close-mark JSON file. Kite derivation selects the latest two-sided, positive-depth NSE bid in the session, verifies the normalized-event digest and raw quote hash shape, and enforces the configured source age and quote availability age. It refuses held symbols without a qualifying quote. The generated mark files retain the Kite source hash and availability time. A Kite source tag is provenance evidence in the local journal, not independent proof of a real provider session. Optional prior rows can be supplied with `--history-sessions`. The generated manifest is content-hashed and immutable at its output path; edits are detected by preflight. The hash detects accidental changes, not authorship or authorization.

## Manifest contract

The manifest is an immutable generated JSON file or an operator-managed JSON file. It contains:

```json
{
  "session_date": "YYYY-MM-DD",
  "captured_at": "aware ISO-8601 exchange-session timestamp",
  "registry": "path/to/registered-paper-books.jsonl",
  "plan_id": "frozen-plan-id",
  "account_root": "path/to/registered-account-journals",
  "books": [
    {
      "strategy_id": "registered-strategy-id",
      "session_id": "durable-forward-session-id",
      "runner_journal": "path/to/forward-runner.jsonl",
      "marks": "path/to/owner-observed-close-marks.json",
      "archive_dir": "path/to/eod-archives",
      "bundle_root": "path/to/portable-bundles"
    }
  ],
  "history_sessions": [],
  "report_output_root": "path/to/immutable-book-reports"
}
```

`books` must name every strategy in the selected frozen plan exactly once. Account-journal paths are derived from the registry and account root; callers cannot redirect a strategy to another account. Before closing an account, the command verifies the runner journal hash chain and requires a matching exchange date, durable terminal session event, registered `authority_strategy_id`, and allocated `authority_account_id`. Close marks remain explicit owner-supplied evidence and must satisfy the EOD archive's timestamp, coverage, and source-hash checks. `history_sessions` carries prior session evidence needed for a multi-session report; it is not reconstructed or inferred by this command.

The cycle is resumable per book, not an atomic multi-account transaction. EOD archives, bundles, and report directories are immutable and retryable for unchanged inputs. If a later book fails, earlier books may already be closed; rerun with the same captured timestamp and marks after correcting the blocking input. The command never sends broker orders or invents missing market evidence.

An OS scheduler can invoke this command after the session capture process writes a complete manifest. This repository does not install or enable a Windows Task Scheduler or systemd job, and no prospective Kite session is implied by a successful fixture test.

## Configured scheduled run

`daily-paper-run --config <path> --session-date YYYY-MM-DD` derives the capture time from matching hash-chain-verified `SESSION_CLOSED` receipts, creates the immutable manifest, runs preflight, and finalizes the cycle. The manifest is stored under `manifest_root/<session-date>/<plan-id-hash>.json`. Retries recompute the same inputs and reuse the same manifest; changed inputs fail the immutability check. The owner-managed config contains paths and policy only:

```json
{
  "schema_version": 1,
  "registry": "data/forward/books.jsonl",
  "plan_id": "paper-plan-2026-11",
  "account_root": "data/forward/accounts",
  "runner_journals": ["data/forward/runners/momentum.jsonl"],
  "archive_dir": "data/forward/archives",
  "bundle_root": "data/forward/bundles",
  "report_output_root": "data/forward/reports",
  "manifest_root": "data/forward/manifests",
  "max_mark_age_seconds": 30,
  "history_sessions": "data/forward/history-sessions.json"
}
```

Relative paths resolve from the config file. `history_sessions` is optional; without it, the report contains only the selected session and is not a multi-session performance record. The Windows wrapper accepts `FB_DAILY_CYCLE_CONFIG` and derives the exchange date using the Windows India time zone. `scripts/register_paper_daily_task.ps1` prepares a weekday Task Scheduler entry for the wrapper, using the current interactive user and local system schedule time (default 16:15). Inspect the proposed registration with `-WhatIf`; actual registration is an explicit operator action. The installer does not supply authoritative market inputs or attest that a local Kite-tagged event came from a provider. The scheduled task runs only while its interactive user is logged on.

## Windows scheduled-task wrapper

`scripts/paper_daily_finalize.cmd` changes to the repository root, requires the repository `.venv`, logs to `data/paper_reports/daily-cycle-YYYY-MM-DD.log`, and returns nonzero on failure. With `FB_DAILY_CYCLE_CONFIG`, it invokes `daily-paper-run` to derive the manifest from verified closed runner sessions, preflight, and finalize. With `FB_DAILY_CYCLE_MANIFEST`, it runs `daily-paper-preflight` followed by `daily-paper-cycle`. `scripts/run_hidden.vbs` can wrap the command for Task Scheduler. The repository still does not create or enable the Windows task or fabricate provider evidence.
