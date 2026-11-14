# Registered paper-book daily cycle

`python -m financial_brain.forward_edge.cli daily-paper-cycle --manifest <path>` finalizes the accounts in one frozen `RegisteredPaperBooks` plan, creates each paper close archive and portable evidence bundle, then writes the by-book performance report.

Run `python -m financial_brain.forward_edge.cli daily-paper-preflight --manifest <path>` first. Preflight reopens every existing account from its durable journal, validates the account against frozen allocated capital, checks every close mark and outstanding order state, verifies every dated runner session and strategy/account binding, and validates the supplied historical report rows. It does not write to account journals or close books. The cycle repeats these checks immediately before it begins.

## Manifest contract

The manifest is an operator-owned JSON file. It contains:

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

## Windows scheduled-task wrapper

`scripts/paper_daily_finalize.cmd` changes to the repository root, requires the repository `.venv`, reads an operator-managed `FB_DAILY_CYCLE_MANIFEST` path, runs `daily-paper-preflight`, and only then runs `daily-paper-cycle`. It logs both commands to `data/paper_reports/daily-cycle-YYYY-MM-DD.log` and returns a nonzero exit code when either phase fails. `scripts/run_hidden.vbs` can wrap this command for Task Scheduler. Configure the environment variable and schedule only after the session capture process has durably written the manifest and marks; this repository does not create the Windows task or fabricate those inputs.
