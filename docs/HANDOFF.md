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
- [x] **P1-1b C06 BSE announcements** — done for 2026-06-20→09-18 (101,461, classified). **History: `data/prefetch_ann.log` shows the background prefetch 2015→2026-06; when it finishes, load with `fb announcements --start 2015-01-01 --end 2026-06-19` (lake-first, ~0.35 s/day).** Original scope: — ingest NSE + BSE corporate announcements through
  the same lake → manifest → quality → curated path; classify event type
  (results, dividend/split/bonus, order win, promoter activity, pledge, rating, …) with
  deterministic rules first. Link events to ISIN via the security master. Use them to
  resolve `needs_source` gaps where an announcement explains the move.
- [x] **P1-2 C07 Market Regime Brain** — done: `fb regime --build` (v2, 12/13 known episodes). Original scope: — from index history (P0-1), breadth from
  universe snapshots, India VIX, FII/DII flows. Deterministic, versioned classifier;
  backtest its stability across 2015–2026 (it must call 2020-03 risk-off).
- [x] **P1-3 C09 Evidence ledger** — done 2026-09-19 (`evidence/ledger.py`, `fb trace`).
- [x] **P1-4 C08 World state** — done 2026-09-19 (`worldstate/build.py`, content-addressed).
- [x] **P1-5 C10 Daily brief** — deterministic cited brief done: `fb brief [--date]` →
  `data/briefs/<date>.md`. Still owner-gated: portfolio section (Kite) and the LLM prose
  layer. Optional personal watchlist: `data/watchlist.txt`.
- [~] **P1-6 Announcement history load** — **loaded 2026-09-19: 3,079,172 announcements,
  2015-01-01→2026-09-18, 4,239 days** (resume run ok=2634, skipped=10). Step 1 below
  (forced replay, 1.40M truncated headlines to repair) started → `data/replay_ann.log`.
  **After it finishes, run in order (one writer at a time):**
  1. `fb announcements --start 2015-01-01 --end 2026-09-18 --force` — reclassifies with
     classifier v2 and repairs headlines stored cut off ("....") from BSE's MORE field.
  2. `fb graph --build` (P2-4), `fb features` (P2-1), `fb gaps --auto-review --redo`.
  3. `fb evaluate --horizon 20` and `fb daily` (P1-7). Record results here.
  Original note: prefetch finished (4,148 days, 4.2 GB, 0
  failures). Load was started 2026-09-19 → `data/load_ann.log`. Verify:
  `SELECT COUNT(*), MIN(business_date) FROM announcements` (expect millions from 2015).
  If incomplete, re-run `fb announcements --start 2015-01-01 --end 2026-06-19`
  (idempotent, lake-first). Then re-run `fb gaps --auto-review --redo`: announcements
  (e.g. CLARIFICATION, SCHEME) may explain more of the 65 open gaps.
- [ ] **P1-7 Daily operation** — a single `fb daily` that runs, in order: bhavcopy
  ingest (today), index, corporate-action feed (this month), announcements (today),
  derive, regime --build, brief. Idempotent, so safe to schedule.

### Phase 2 — Research and committee (after P1-6, P1-7)

Plan: `docs/BUILD-FLOW.md` §4. Order chosen so nothing needs owner credentials first:

- [x] **P2-1 C12 Feature service** — done (code+tests): `features/prices.py` (continuous
  adjusted series across ISIN successions, ASOF step join), `features/indicators.py` (f1:
  returns, 12-1 momentum, vol, 52w distance, MAs, ADV20; NULL until the window is full).
  `fb features`. Full build pending the DB (see P1-6). Original scope: — deterministic, versioned indicators/factors from
  adjusted prices (uses adjustment_factors + isin_successions for continuous history).
- [x] **P2-2 C17 Decision record & lifecycle** — immutable decision contract + state
  machine (ARCHITECTURE.md §11); every decision references one world_state version.
- [x] **P2-3 C21 India evaluation benchmark** — done: factor rank-IC benchmark
  (`evaluation/benchmark.py`, `fb evaluate`) and classifier precision on hand-checked
  samples (`evaluation/labels.py`, `fb evaluate --classifier`): v1 88.7% on the tuning
  sample; v2 94.1% on a held-out sample before any change it prompted. RBI hawkish/dovish
  still to do (needs RBI statements ingested). Original scope: — start with labelled announcement types
  (measure classifier precision on a hand-checked sample) and RBI hawkish/dovish.
- [x] **P2-4 C24 Knowledge graph** — done: filers extracted from SAST/insider headlines
  (`graph/extract.py`), promoter groups on corroborated links only (`graph/build.py`,
  `fb graph --build`, `fb graph --isin X`). Prototype on 148k lake disclosures recovered
  Tata, JSW/Jindal, Adani, Adventz, Future, Godrej, Max; Birla family merges via Pilani
  (stated limit).
- [~] **P2-5 C11/C14/C16** — groundwork done: Investment Constitution enforced at
  RISK_REVIEWED (`constitution/rules.py`, example in `docs/constitution.example.toml` —
  **owner: write `data/constitution.toml`**); closed-by-default LLM gate (`llm/gate.py`,
  `FB_LLM_ENABLED=1` + SDK credentials; whitelisted purposes; every call recorded). Still
  to build: document intelligence over filings, the committee agents. Original: — document intelligence, Investment Constitution, committee:
  need an LLM API (owner) — build interfaces and tests, gate the model calls.

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
- **Autostart needs one-time tool approval.** The hourly `financial-brain-resume` task
  restarts correctly after a limit reset, but its run at 2026-09-18 21:51 UTC sat on a
  permission prompt at its first shell command. Owner: open *Scheduled* in the sidebar →
  `financial-brain-resume` → **Run now**, and approve its tools ("always allow"); approvals
  persist for future runs. (Runs during the limit itself fail by design.)

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
- 2026-09-19 — P1-1b announcements (classifier fixed on real residue: tax demands vs
  order wins); index lineage across NSE renames; P1-2 regime brain v2. Announcement
  history prefetch running in background (see checklist). Next: P1-3 evidence ledger.
- 2026-09-19 — P1-3 evidence ledger, P1-4 world state, P1-5 cited daily brief
  (`fb brief`, `fb trace`). First real brief read as a reader drove 8 fixes (see commit
  55b70d5). Announcement history load started (P1-6). Found autostart blocked on a
  permission prompt; noted under Needs the owner.
- 2026-09-19 (later) — P2-1 features, P2-3 benchmarks, P2-4 knowledge graph, P2-5
  constitution + LLM gate (commits 3374cc0..f8e018e). Found BSE truncates HEADLINE (full
  text in MORE) - fixed; needs a --force replay. Announcement load resumed from 2019-03-25.
