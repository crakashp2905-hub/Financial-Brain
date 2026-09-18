# Handoff — read this first

This file is the durable checkpoint for work on Financial-Brain. Sessions end — usage
limits, restarts, the app closing — and a new session has none of the previous
conversation. **Everything a resuming session needs is here.** Keep it current: tick
items as they finish, and add anything the next session must know.

Owner's standing instruction (2026-09-18): *"Once every gap has been filled, start
Phase 1. If session limits hit, autostart after the limit period is over."*

---

## Resume protocol

1. **Check for a live session.** Read `data/.heartbeat` (a UTC ISO timestamp). If it is
   **less than 45 minutes old**, another session is working — **stop immediately** and
   do nothing. The warehouse is single-writer; two sessions would corrupt each other's
   runs.
2. Otherwise claim the work: write the current UTC time to `data/.heartbeat`, and
   rewrite it at least every ~20 minutes while working (before and after every long
   step). Long jobs: run them in the background and keep touching the heartbeat while
   waiting.
   ```bash
   ./.venv/Scripts/python.exe -c "import datetime,pathlib; pathlib.Path('data/.heartbeat').write_text(datetime.datetime.now(datetime.timezone.utc).isoformat())"
   ```
3. Check no stray writer is running: `tasklist //FI "IMAGENAME eq python.exe"` and
   look for `financial_brain` command lines. If one is mid-job and the heartbeat is
   stale, it was orphaned — let it finish if it is progressing (CPU time rising),
   otherwise stop it. DuckDB recovers from a hard stop via its WAL, and everything
   downstream of the lake is rebuildable.
4. Work down the checklist below from the first unticked item.
5. Before stopping for any reason: update this file, commit, push, and delete
   `data/.heartbeat` so the next scheduled run can start.

---

## Working rules

- Repo: `D:\Finance-Brain`, remote `crakashp2905-hub/Financial-Brain` (private), branch `main`.
- Python: `./.venv/Scripts/python.exe`. CLI: `./.venv/Scripts/python.exe -m financial_brain.cli <cmd>`.
- Tests: `./.venv/Scripts/python.exe -m pytest tests/ -q` — all offline, must pass before any commit.
- Commit as `git -c user.email=crakashp2905@gmail.com -c user.name="Akash Preetham" commit`,
  message ending with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. Push to `main`.
  Commit messages explain *why*, and report measured results.
- **Never commit `data/`** — large, rebuildable, and exchange data may not be redistributed.
- **One database writer at a time.** Never run two `financial_brain` jobs that write.
- **Lake first.** Never re-fetch bytes the lake holds. Backfills: `fb prefetch` (parallel,
  no DB) then `fb ingest --from-lake` (serial, no network). Replaying an
  already-published day needs `--force`.
- **Never enter credentials, API keys or tokens anywhere, and never fabricate them.** If a
  step needs one (Kite, an LLM API), build everything that does not, leave the
  credential-dependent path behind a clearly-failing config check, and list it under
  *Needs the owner* below.
- Quality over speed. If a check fails, find the cause in the data before changing the
  check — both 11-year-backfill "failures" turned out to be real market structure
  (BSE twin scrip codes; UPL preference shares with no group code).
- Record findings in `docs/PHASE-0.md` / the relevant phase doc, and keep
  `brain/` (Obsidian vault) component notes' `status:` in sync with reality.

---

## Current state (2026-09-18, evening)

- **Prices:** NSE + BSE bhavcopy, **2015-01-01 → 2026-09-18, 2,903 sessions each, zero
  one-sided days** (the two exchanges share one calendar - that agreement is the
  completeness check). Includes weekend special sessions (Budget, Muhurat, 2024 DR
  drills), which the old weekdays-only calendar never fetched.
- **BSE before 2016-12-08** has no ISIN in the source; rows are resolved by scrip code
  with same-day NSE corroboration (`ingest/bse_isin.py`) - ~45% of rows, essentially
  the whole cross-listed set; BSE-only scrips for that period are unrecoverable.
- **Index levels:** 2015 → 2026, 228 indices. **9 NSE sessions in 2015-16 have no index
  file** (genuine 404s: 2015-02-02, 03-12, 03-13, 05-19, 07-08, 09-04, 10-16, 12-01,
  2016-06-20). `fb kite-index-fill` can fill them once Kite credentials exist.
- **Guards added today:** stale-republication detection (BSE re-served Friday as
  Saturday 2020-06-20 / 10-17); 403 is a failure, only 404 means "not published";
  consecutive-session rule and open-gap / price-fall / market-shock guards in
  corporate-action inference.
- Tests: 98 passing. `isin_successions` links companies across split-driven ISIN changes. Lake ~1.1 GB, fully registered in `lake_manifest`.

## Checklist

### Phase 0 — close every gap

"Every gap filled" means: all data loaded for 2015–2026, corporate actions derived over
the whole history, **every** unexplained price gap triaged, and `fb gate` passing on the
full dataset.

- [x] **P0-1 Index history loaded.** (done 2026-09-18; 9 source gaps listed above) Verify:
  `SELECT COUNT(DISTINCT business_date), MIN(business_date) FROM index_levels` → ~2,880
  days from 2015. If short, re-run `fb index --start 2015-01-01 --end 2026-09-16`
  (idempotent, lake-first). Sanity: Nifty 50 bottomed on 2020-03-23.
- [x] **P0-2 Derive corporate actions** (done: 895 actions — 237 gap, 658 ISIN succession; 375 gaps reviewed, 0 untriaged) over the full history: `fb derive --rebuild` (rebuild clears derived rows first). Then `fb gaps --auto-review` and confirm `fb gaps` shows zero untriaged. Record counts.
- [x] **P0-3 Test `fb gaps --auto-review`** — `auto_triage_gaps` in
  `src/financial_brain/corpactions/detect.py`, tested 2026-09-18 for all rules: shock
  day / intraday move / one-sided on a cross-listed ISIN → `price_move`; single-listed
  or both-gapped without a clean ratio → `needs_source`. Running it is part of P0-2.
- [x] **P0-4 `fb gate`** (done 2026-09-18: all 12 bases pass) on the full dataset — every base must pass. Any failure: find
  the cause, fix, re-run. Do not weaken a check to make it pass.
- [x] **P0-5 Document** the 11-year results in `docs/PHASE-0.md` (coverage, counts,
  quarantine causes, gap-triage outcome, gate result) and update `README.md` status.
  Commit + push.

### Phase 1 — Perception (start only when P0-1…P0-5 are ticked)

Plan: `docs/BUILD-FLOW.md` §3. Exit test: *a cited daily brief you would actually read
before the market opens.* Order:

- [x] **P1-1a C06 BSE corporate-action feed** — done 2026-09-19: `fb corpact-feed`
  (22,274 reported actions 2015–2026), `fb corpact-reconcile` (derived precision 73.7%,
  recall 67.9%), reported supersedes derived in factors, gaps open 201 → 65. See
  `docs/PHASE-1.md`. Monthly: re-run `fb corpact-feed --start <this month>`.
- [ ] **P1-1b C06 Event intelligence** — ingest NSE + BSE corporate announcements through
  the same lake → manifest → quality → curated path; classify event type
  (results, dividend/split/bonus, order win, promoter activity, pledge, rating, …) with
  deterministic rules first. Link events to ISIN via the security master. Use them to
  resolve `needs_source` gaps where an announcement explains the move.
- [ ] **P1-2 C07 Market Regime Brain** — from index history (P0-1), breadth from
  universe snapshots, India VIX, FII/DII flows. Deterministic, versioned classifier;
  backtest its stability across 2015–2026 (it must call 2020-03 risk-off).
- [ ] **P1-3 C09 Evidence ledger** — immutable provenance chain per ARCHITECTURE.md §5.4.
- [ ] **P1-4 C08 World state** — versioned immutable snapshot built from C06 + C07 + C09.
- [ ] **P1-5 C10 Daily brief** — "what changed since yesterday", every line cited to the
  evidence ledger. Market/sector section needs no credentials. Portfolio section needs
  Kite (read-only) and the prose layer needs an LLM API — both owner-supplied; build
  the deterministic brief first.

---

## Needs the owner

- **Kite Connect credentials** — set `KITE_API_KEY` and `KITE_ACCESS_TOKEN` in the
  environment (the token comes from Kite's daily login). Unlocks `fb kite-index-fill`
  (the 9 missing index sessions) and the portfolio section of the daily brief (P1-5).
  Never ask for, store or invent these.
- **LLM API access** — first LLM use is Phase 1 summarisation (never prediction).
- **Decision D1 — decided 2026-09-19:** personal use now; product only if it works out.
  Keep AGPL dependencies behind our own interfaces; never redistribute exchange data.
- **Kite credentials** — owner will provide later; do not block on them.

## Session log

- 2026-09-18 — 11-year backfill complete; fixed replay-skips-check, prefetch provenance
  gap, lake-blind index job, blank-series constraint. Set up this handoff and the
  hourly `financial-brain-resume` scheduled task.
- 2026-09-18 (later) — BSE 2015-16 recovered via NSE-corroborated scrip mapping; weekend
  special sessions found and loaded (calendar is now every calendar day); republication
  guard; 403 ≠ 404; corporate-action inference corrected (8,944 derived "actions" were
  ~all artefacts of missing sessions and series transfers - Detector A finds zero real
  restatements; gap detection now guarded). Kite provider built, awaiting credentials.
  Owner asked about TradingView / Investing.com: declined (no API; scraping breaches
  their terms and needs bot-evasion). yfinance is grey - cross-check use only.
- 2026-09-18 (night) — ISIN successions (864 found, 658 splits recorded; ratio measured
  across the ex-date because exchanges switch on different days); one shared untriaged-gap
  definition (the gate's drifted copy reported 2,201 phantom gaps). **Phase 0 gate: 12/12.
  Phase 0 closed; Phase 1 begins with P1-1.**
- 2026-09-19 — P1-1a: BSE corporate-action feed ingested and reconciled against
  derivation. Found and fixed double adjustment across ISIN successions (Yes Bank 2017),
  and derivation suppressing itself when reported actions existed. Next: P1-1b
  announcements (BSE `AnnSubCategoryGetData` works without a session, 50 per page).
