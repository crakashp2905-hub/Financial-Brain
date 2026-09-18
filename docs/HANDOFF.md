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

## Current state (2026-09-18)

- **Prices:** NSE + BSE bhavcopy, **2015-01-01 → 2026-09-16, all 2,890 trading days
  resolved**. Legacy (pre-July-2024) formats are normalised into UDiFF; verified
  equivalent on the overlap (2,806 rows, zero mismatches).
- **Scale:** 14.7M universe rows, 16,864 ISINs, 42,543 listings, 8,186 lake objects
  (~1 GB), all registered in `lake_manifest`.
- **Indexes:** covering indexes added on `universe_snapshots`, `ingest_runs`,
  `security_listings`, `corporate_actions`.
- **Index history:** 2,881 NSE index-close files prefetched into the lake;
  `fb index --start 2015-01-01 --end 2026-09-16` was loading them (lake-first) when this
  file was written. Verify with the query in step P0-1.
- Tests: 65 passing. Last commit `2553054`.

---

## Checklist

### Phase 0 — close every gap

"Every gap filled" means: all data loaded for 2015–2026, corporate actions derived over
the whole history, **every** unexplained price gap triaged, and `fb gate` passing on the
full dataset.

- [ ] **P0-1 Index history loaded.** Verify:
  `SELECT COUNT(DISTINCT business_date), MIN(business_date) FROM index_levels` → ~2,880
  days from 2015. If short, re-run `fb index --start 2015-01-01 --end 2026-09-16`
  (idempotent, lake-first). Sanity: Nifty 50 bottomed on 2020-03-23.
- [ ] **P0-2 Derive corporate actions** over the full history: `fb derive`. Record counts.
- [ ] **P0-3 Test `fb gaps --auto-review`** (written but *not yet tested* — see
  `auto_triage_gaps` in `src/financial_brain/corpactions/detect.py`). Add offline tests
  for all three rules (one-sided on a cross-listed ISIN → `price_move`; single-listed →
  `needs_source`; both-gapped without a clean ratio → `needs_source`), then run it.
  Check `fb gaps` shows zero untriaged.
- [ ] **P0-4 `fb gate`** on the full dataset — every base must pass. Any failure: find
  the cause, fix, re-run. Do not weaken a check to make it pass.
- [ ] **P0-5 Document** the 11-year results in `docs/PHASE-0.md` (coverage, counts,
  quarantine causes, gap-triage outcome, gate result) and update `README.md` status.
  Commit + push.

### Phase 1 — Perception (start only when P0-1…P0-5 are ticked)

Plan: `docs/BUILD-FLOW.md` §3. Exit test: *a cited daily brief you would actually read
before the market opens.* Order:

- [ ] **P1-1 C06 Event intelligence** — ingest NSE + BSE corporate announcements through
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

- **Kite Connect API key/secret** — for the portfolio section of the daily brief (P1-5).
- **LLM API access** — first LLM use is Phase 1 summarisation (never prediction).
- **Decision D1: personal tool or product?** — see `docs/RESOURCES.md` §11.

## Session log

- 2026-09-18 — 11-year backfill complete; fixed replay-skips-check, prefetch provenance
  gap, lake-blind index job, blank-series constraint. Set up this handoff and the
  hourly `financial-brain-resume` scheduled task.
