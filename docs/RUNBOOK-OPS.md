# Operations runbook (data, paper session, backup)

Broker, OMS and kill-switch incidents are in `RUNBOOK-LIVE.md`. This covers the daily loop.

## Every trading day

| time (IST) | what | who |
|---|---|---|
| before 08:30 | `scripts\kite_login.cmd` (Zerodha login; tokens expire about 06:00) | owner |
| 08:30 | `fb preflight`: network, Kite token, disk (50 GB), research DB opens read-only, every data contract met. A failure alerts once per distinct set of failures, by desktop toast and, if configured, Telegram | task |
| 09:05 | live paper session (`scripts\paper_intraday.cmd`) | task |
| 18:45 | nightly refresh (project venv), then `fb backup --with-lake` as the last step | task |

The preflight cannot know exchange holidays. On a holiday a missing login is expected: ignore the alert.

Register the preflight task once, yourself: `powershell -ExecutionPolicy Bypass -File scripts\register_preflight_task.ps1`.

Phone alerts: create your own Telegram bot, then set `FB_ALERT_TELEGRAM_BOT_TOKEN` and `FB_ALERT_TELEGRAM_CHAT_ID` in `.env`
(outside git). Until then alerts are desktop toasts only, which are easy to miss.

## A paper session aborted (`runs.status = 'aborted'`)

1. `data\paper_reports\runner-<date>.log` has the reason. "no Kite access token" means the login was missed; "getaddrinfo
   failed" means the network was down for 30 minutes (90 polls, 20 s apart).
2. The day is **not** forward evidence and is not repaired: a gap in quotes would corrupt stops and fills. Rebuild a
   labelled replay only for study: `fb paper-intraday --replay <date>`. Replay and live are never pooled.
3. Do not edit `runs` or the trades. A missing day stays missing.

## The nightly refresh failed

`data\paper_reports\refresh-<date>.log`. Every step is independent, so one failure does not stop the rest. Re-run only the
failed command; collectors are idempotent. The research DB is single-writer: never run a writer between 09:05 and 15:30.

## Backup and restore

```
fb backup                    # dated, verified snapshot of the small databases (default FB_BACKUP_DIR = C:\FB-backup)
fb backup --with-lake        # also mirror the raw lake to FB_BACKUP_LAKE_DIR (E:\FB-lake); append-only, never deletes
fb backup --verify           # re-hash the latest snapshot against its manifest and reopen each database
```

A database a writer holds is reported as SKIPPED and the exit code is 1; run backup when the runner and refresh are idle.
Seven dated snapshots are kept. **Restore drill:** copy a database out of a snapshot into a scratch folder, open it with
`duckdb.connect(path, read_only=True)` and compare table counts with `manifest.json` (this is what `--verify` does). To
restore for real, stop every writer, then copy the file back over `data\<name>.duckdb`.

What is *not* backed up: `financial_brain.duckdb`, `fo_history.duckdb`, `minute_history.duckdb` (rebuildable from the lake,
and for minute bars from Kite, which has no delisted names), `.env` and the Kite token (secrets).
D: and E: are one physical disk. An external or off-site copy is the remaining gap.
