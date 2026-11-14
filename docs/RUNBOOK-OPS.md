# Operations runbook (data, paper session, backup)

Broker, OMS and kill-switch incidents are in `RUNBOOK-LIVE.md`. This covers the daily loop.

## Every trading day

| time (IST) | what | who |
|---|---|---|
| before 08:30 | `scripts\kite_login.cmd` (Zerodha login; tokens expire about 06:00) | owner |
| 08:30 | `fb preflight`: network, Kite token, disk (50 GB), research DB opens read-only, every data contract met, and the depth archive task is registered (its own alert). A failure alerts once per distinct set of failures, by desktop toast and, if configured, Telegram | task |
| 09:05 | live paper session (`scripts\paper_intraday.cmd`) | task |
| 09:10 | depth and open-interest archive (`scripts\depth_archive.cmd`): waits for the login until 10:30, exits on a holiday and at 15:31 | task |
| 18:45 | nightly refresh (project venv): loss review, loss miner, shadow, depth-archive health check, then `fb backup --with-lake` as the last step | task |

The preflight cannot know exchange holidays. On a holiday a missing login is expected: ignore the alert.

Register the preflight task once, yourself: `powershell -ExecutionPolicy Bypass -File scripts\register_preflight_task.ps1`.
Register the depth archive task once, yourself: `powershell -ExecutionPolicy Bypass -File scripts\register_depth_archive_task.ps1`.

Phone alerts: create your own Telegram bot, then set `FB_ALERT_TELEGRAM_BOT_TOKEN` and `FB_ALERT_TELEGRAM_CHAT_ID` in `.env`
(outside git). Until then alerts are desktop toasts only, which are easy to miss.

## The depth and open-interest archive

Why: the V2 intraday trial (35 of 35 candidates failed) showed that direction rules earn less than their cost, and that an
intraday edge would need information price alone does not carry: the order book and intraday open interest. Nobody sells that
history for Indian equities; the only way to have it is to collect it, and every missed week is lost for good.

```
fb depth-archive            # the task runs this; one session, 09:15-15:30, one poll a minute
fb depth-status             # per-session coverage, the 120-session readiness gate, and whether the newest session is complete
fb depth-status --alert     # the nightly refresh runs this: an alert if the newest session was not collected
```

* **What is stored** (`data\depth_archive.duckdb`, `quotes`): last price, VWAP, volume, OI, and the best bid/ask plus the total
  quantity over five levels, for every F&O stock, its near future, and NIFTY and BANKNIFTY options 10 strikes either side of the
  money; one run row per day in `runs` says how it ended (complete, closed, no_login, aborted).
* **Self-healing:** it waits for a late Kite login (until 10:30), removes the day's rows if no trade printed by 09:25 (a holiday),
  refuses to start twice, and skips a failed minute. It never places an order.
* **A day is OK** with at least 300 polls, a median of 200 names a poll and a book on at least 90% of rows. `fb depth-status`
  shows OK, PARTIAL (late login or a failed stretch), EMPTY (the task did not run) or CLOSED (a holiday).
* **When it is enough:** 60 complete sessions describe spreads and depth; **120** (two halves of 60, about six months) are the
  least that let ADR-0004's both-halves gate mean anything, so an order-flow or open-interest hypothesis is registered no earlier
  than that. 2026Q4's budget is spent in any case; the earliest registration is 2027Q1.
* **Rate limit:** the paper runner and this archive both poll Kite's quote endpoint from separate processes (about 1 call per 20 s
  and 2-3 calls a minute, against a limit of 1 call a second). If `runs.failed_polls` is regularly above zero in either, stagger them
  before changing anything else.
* **Licence:** the owner's check. Kite Connect's terms govern storing and using market data; this archive is local, for the
  owner's own research, and must not be redistributed. Read the current terms before relying on it for anything else.
* **Size:** about 170 thousand rows a day; the backup copies the whole file each night (seven snapshots), which is fine for a
  year and worth revisiting after that.

## Learning from mistakes

The loop is described in `docs/LEARNING-LOOP.md`. Day to day:

```
fb learn run        # the nightly refresh runs this after the close (it reads the paper ledger, so never during 09:05-15:30)
fb learn status     # what it learned, what it is waiting for, and what needs you - read this first
fb learn autopsy    # how each losing trade lost, in rupees by cause
fb learn incidents  # lost sessions by cause, and whether each cause has an active guard
fb learn calibrate  # measure the loop's own false-confirmation rate and power on your ledger (a few minutes; run it once in a while)
```

* An alert "a loss pattern repeated on new sessions" is a **request for a decision**, never a change: `fb learn status`, then `fb learn draft
  <lesson>`, commit the draft as a pre-registration, `fb learn register`. After 60 sessions the shadow says PASSING or not; only you can
  `fb learn approve` it, at a terminal.
* The paper `live-v2` cohort (`scripts\paper_intraday_v2.cmd`, task registered once with `scripts\register_paper_v2_task.ps1`) is for
  approved rules only; with none active it exits at once. Compare it with live-v1 using `fb learn ab`.
* An operational cause marked GUARD NOT ACTIVE means a control exists in code but is not running: register the task it names.
* `fb learn run` skips itself politely while the live run holds the ledger; run it again after 15:30.

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
