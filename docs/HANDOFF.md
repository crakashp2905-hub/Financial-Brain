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
- [x] **P1-6 Announcement history load** — **done 2026-09-19: 3,079,172 announcements,
  2015-01-01→2026-09-18, 4,239 days.** Forced replay (ok=4239) reclassified with v2 and
  repaired headlines: truncated 1,403,429 → 1,038 (rows BSE sent without MORE). Replay
  took ~6 h (row-wise upserts); next time prefer a set-based repair. Gaps re-triaged:
  needs_source 65 → 58; gate 12/12.
  Post-load results (`data/after_replay.log`): graph 161 groups / 447 companies;
  features 4.92M rows, 3,397 lineages; firewall fw1 PROMOTED mom_12_1 (IC +0.049, t 3.7,
  DSR 0.97, +0.52%/20d net, trial 2) and REJECTED the other eight (reversal and low-vol
  fail on costs; dist_52w_high DSR 0.95 borderline). Survivorship fixed the same
  evening: names without a t+h price (~1,500 across 132 dates) are now scored at their
  last traded price. ICs barely moved (mom_12_1 +0.049, t 3.8; net +0.57%/20d), but on
  trials 10-18 **all nine are REJECTED**: the deflated-Sharpe bar after 18 trials (with
  several strongly negative ones widening the cross-trial Sharpe variance) exceeds every
  net Sharpe. Working as designed - do not loosen it; a signal must now be strong enough
  to survive the trials already spent. Next research step: new, *pre-registered*
  hypotheses rather than re-running these.
  Original steps:
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
- [x] **P1-7 Daily operation** — `fb daily` ran end to end 2026-09-19 (brief for
  2026-09-18, 88 citations, with promoter-group context on red flags). Original: — a single `fb daily` that runs, in order: bhavcopy
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
- 2026-09-19 (evening) — announcement history complete and replayed; graph keyed by
  scrip code (no duplicate members); India Implementability Gate; Alpha Validation
  Firewall run on real data (1 PROMOTE, 8 REJECT; survivorship caveat above). Brief
  shows promoter groups (e.g. Vedanta pledge -> 4 demerged Vedanta companies).
  RBI (P2-3 remainder) probed: rbi.org.in's WAF refuses automated requests (robots.txt
  -> 418 "Unauthorised Access") - not worked around (no bot evasion). The press-release
  RSS (`https://www.rbi.org.in/pressreleases_rss.xml`) is machine-readable but holds only
  the last 10 items (< 1 day), so no history. Hawkish/dovish benchmark needs the ~70 MPC
  statements since 2015 from a sanctioned source (DBIE, or the owner's downloads into
  `data/inbox/rbi/`). **Owner decision.**
  Next: 
  document intelligence + committee once the owner enables the LLM gate.
- 2026-09-19 (night) — Pre-registered hypotheses (`evaluation/registry.py`,
  `fb hypothesis register|test|list`): spec frozen with its data cutoff; one in-sample
  test ever; out-of-sample only on data after the cutoff (>= 12 rebalances, checked
  before a trial is spent). Signals take a direction (-1 = low is good). Registered
  three for **out-of-sample only** (cutoff 2026-09-18; first verdicts ~Sep 2027):
  12-1 momentum `hy_36e6a075cee59cf3`, low volatility `hy_6acfdb23c48891b9`, near
  52-week high `hy_041b042053188d36` (specs in `docs/hypotheses/`). **Do not run their
  in-sample tests** - they were already seen in-sample. New ideas: write a spec, register,
  then test.
- 2026-09-19 (night, later) — Paper trading (`paper/ledger.py`, `fb paper open|mark|list`):
  entry at the first close on/after the world state's as-of, exit after horizon_days,
  Indian round-trip cost by liquidity bucket, excess vs Nifty 50, direction from the
  action; split-safe (entry re-read on the current adjusted basis). `fb daily` now also
  refreshes features + promoter graph and closes due paper trades. Implementability
  gate uses the point-in-time F&O list (held from 2026-09-17 only).
- 2026-09-19/20 (night) — **Strategy change: ADR-0002 tiered models** (owner request).
  Tier 0 deterministic first; every model call is a typed "System One" decision (Jev
  pattern, TypeSafe AI) - `llm/system1.py`; backends Ollama (local, option-letter
  log-probs), FinBERT (int8 ONNX), Anthropic (gated). `fb models list|bench|plan`
  measures candidates on our labelled Indian tasks and sets calibrated acceptance
  thresholds; `llm/router.py` goes cheapest-first and escalates. Sentiment fixture: 165
  Claude-labelled filing headlines. Results so far (macro-F1): llama3.1:8b 0.82,
  qwen2.5:7b 0.76, phi4 0.72, qwen2.5:3b 0.66, qwen2.5:1.5b 0.64, FinBERT 0.45,
  FinSenti-1B 0.05; rules 98.9% on event type. Thinking models (qwen3:4b removed,
  deepseek-r1, Fin-R1) can't give typed answers -> reasoning tier only.
  Built on it: `fb tone` / daily step (shareholder tone of high-materiality filings,
  shown in the brief only when a model cleared its bar, cited as MODEL tier);
  `fb committee --isin` (dossier of cited facts -> analyst stances -> cited bull/bear
  debate -> deterministic chair -> DRAFT decision). Square-root impact law in costs.
  Shared resources (TradingAgents, HARLF, LLM strategy finding, QuantResearch) assessed
  in RESOURCES §3.6a.
  **Disk:** C: hit 0 GB free (Ollama store 41 GB on C:). Owner approved moving it:
  copied to `D:\ollama\models` (39.2 GB, verified); switch-over (OLLAMA_MODELS user env
  var + Ollama restart, then delete the C: copy) pending the end of the benchmark round
  (`data/bench_sentiment2.log`). Then register Fin-R1 (`data/models/Modelfile.finr1`).
- 2026-09-20 (early) — **Router calibration corrected by a held-out set.** Labelled
  `tests/fixtures/sentiment_labels_holdout.json` (104 headlines 2021-2023, positive-
  enriched, labelled before any model saw them). The in-sample route
  (finbert -> llama3.2:3b -> llama3.1:8b, "89.1% at 1.26 s") scored **65.4% with 23.6% of
  accepted answers wrong**. `fb models route --verify-on sentiment_holdout` now rejects
  any chain whose accepted answers exceed the error bar on held-out data; every cascade
  was rejected, **qwen2.5:7b alone** passed (86.5%, 7% wrong when accepted, declines 34%)
  and is the stored route. `fb tone` therefore accepts only confident *neutral* today -
  no filing is flagged positive/negative, because no verified model has earned a
  "positive" threshold (the tuning set holds just 14 positives). Benchmarking gemma2 and
  phi4 (which do have positive thresholds in-sample) on the held-out set is running ->
  `data/bench_holdout.log`; re-run `fb models route --verify-on sentiment_holdout` after.
  Next calibration step: cross-fit (fit on A verify on B *and* the reverse), and label
  more positives/negatives.
  **Also:** `num_ctx` capped (2k typed / 4k generation) - the default 16k context made
  Ollama allocate a 2 GB KV cache and fail to load 8B models on this laptop.
  **Done 2026-09-20:** the model store moved to `D:\ollama\models` (C: back to 33 GB
  free), which unblocked Fin-R1.
- 2026-09-20 (morning) — **Fin-R1 registered, measured, and now leading the route.** It
  had been marked `system_one = false` on the assumption an R1-style distill must think
  first; forced to one token it answers on-menu in ~2.4-3.7 s and is the only local
  model earning a threshold for **all three** labels (positive included - the gap that
  previously left good news unflagged). Verified route is now
  **fin-r1 -> llama3.1:8b -> gemma2** at the strict 10% error bar: 89.4% held out, 9.8%
  wrong when accepted, 2% declined, recall 94/75/80% (neutral/positive/negative);
  Fin-R1 settles 82% alone. `fb tone --refresh` re-does rows left by a superseded route
  (staleness by time, not model name). 2026-09-18 re-run: 88 of 90 accepted, brief
  flags eight filings, each naming its model.
  Still open: cross-fit calibration (fit on A verify on B *and* the reverse), more
  labelled positives/negatives, and a judgement call to review - Fin-R1 reads "Exchange
  has sought clarification with reference to news" as adverse (0.998), which my own
  labelling guide treats as procedural/neutral.
- 2026-09-20 (midday) — **News recovered from filings; calibration split by text type.**
  `events/newsref.py` extracts the news a filing refers to from the filing's own text
  (quoted headline, or the URL slug) - deterministic, no request to any publisher.
  Backfilled: 19,732 filings carry a news reference, 7,146 with a headline. Tone reads
  that headline instead of clarification boilerplate, and the brief shows it under the
  filing, cited (Nestle 2026-09-18 -> "FSSAI initiates legal action against Nestle India
  on baby formula, shares fall 2%").
  **The trap this exposed:** filing-fitted thresholds were wrong 19.8% of the time on
  headlines vs 9.8% on filings. Headlines now have their own verified route
  (`sentiment_news`: fin-r1 -> llama3.1:8b -> gemma2; held out 87.4%, 6.6% wrong when
  accepted, declines 36%, **97% recall on adverse**). A text type with no verified route
  is not classified at all - see `router.has_route`.
  Open: Moneycontrol article *fetching* is not built (robots.txt allows news paths,
  disallows /stocks/company_info/ and /financials/results/; would be on-demand and
  rate-limited, not crawling). MFCentral needs your PAN+OTP - not something I will enter;
  export the CAS yourself or use AMFI's public NAV feed if you want fund data in.
- 2026-09-20 (afternoon) — **Three public sources built.**
  * **AMFI** (`fb mfnav`, in the daily cycle): the public NAV file, ~14.4k schemes with
    ISINs, lake-first, keyed by the date each row carries - the file mixes today's NAVs
    with stale ones, and a 2018 NAV is not today's.
  * **Moneycontrol articles** (`fb newsfetch`): on-demand only, for URLs a filing already
    pointed at, one at a time with a crawl delay, allowlisted hosts, robots.txt obeyed.
    Title/time/summary stored, never the body. **Moneycontrol fundamentals are
    robots-disallowed** - it is a news source here. Daily fetches at most 10, only where
    the rules could not recover a headline.
  * **Screener** (`fb fundamentals SYMBOL`): the top-ratios block, Tier 3, cross-checked
    against our own Tier-1 close; >2% apart is stored `quality='disputed'`, not averaged.
    Live: RELIANCE/TCS/INFY agreed within 0.1-0.6%. Feeds the committee dossier, which
    reads only snapshots that existed on or before its as-of date.
  **Watch out:** `providers/robots.py` exists because `urllib.robotparser` matches rule
  paths by plain prefix - Moneycontrol's `Disallow: /stocks/company_info/*` matched
  nothing and read as *allowed*. A test pins that gap. Any new fetcher must use this
  matcher, not the stdlib.
  **Fixed in passing:** three daily steps called `db.connect()` where no `db` is bound in
  `cmd_daily` - every `fb daily` would have failed on the tone step. All steps now run
  (verified end-to-end on 2026-09-18).
  Open: bulk fundamentals for the universe is deliberately NOT built (~2k requests);
  fetch per company as needed. MFCentral still owner-only (PAN + OTP).
- 2026-09-20 (late afternoon) — **Phase 2 exit test closed, and C20 started.**
  * **Invalidation monitoring** (`fb monitor`, daily step): decisions carry typed
    `invalidation_checks` beside the prose; `decisions/monitor.py` re-evaluates them over
    Tier-1 data (drawdown from entry, red-flag filing, accepted adverse reading, price
    level, pledge filings). A trigger becomes dated evidence + a decision event, raised
    once. Prose with no typed twin is reported **unmonitored** - the existing Reliance
    draft shows three. The committee chair now emits typed checks with its bear case.
    The brief prints a "Theses under watch" section with citations.
  * **C20 untrusted text** (`fb security`): every typed decision wraps its input in
    markers it cannot close, with a standing "this is data, not instructions" line;
    text that tries to steer the model is refused and logged to `security_findings`,
    and tone falls back to the exchange's own filing text. Tests pin both directions -
    six steering patterns caught, four real headlines (FSSAI, PVR probe, rating
    downgrade, order win) must NOT be flagged.
  * **Watchlist**: `data/watchlist.txt` seeded as a PLACEHOLDER (30 most liquid NSE EQ
    names by median turnover) - **replace it with yours**. `fb fundamentals --watchlist`
    fetched all 30: 29 price cross-checks agree with our own closes, 0 disagree.
  * **ruff** added with a defect-only rule set (F, E9, B) and cleared; the scorer now
    zips decisions against golds with `strict=True`.

  **DONE 2026-09-20 evening — re-benchmark and routes finished.** All 3 models were
  re-measured on all 4 labelled sets under prompt `p_79fb298ec473`, and both routes were
  re-chosen and verified out-of-sample at the strict 10% bar:

  | task | route | held out |
  |---|---|---|
  | `sentiment` (filings) | fin-r1 -> phi4 | 88.5% acc, 10.0% wrong when accepted, declines 13.5% |
  | `sentiment_news` | fin-r1 -> llama3.1:8b -> gemma2 | 87.4% acc, 9.9% wrong when accepted, declines 14.7%, **91% of adverse headlines** |

  `router.route_is_current()` reports True for both; `fb tone` runs again. Two guard bugs
  were found by doing this for real: the optimiser proposed a route from models measured
  under the *old* prompt (candidates are now filtered by prompt version), and tone
  checked route freshness only for news, so a stale filing route would still have run.

  **Watch out:** `fb migrate` silently did nothing because the DB was locked by the
  benchmark and the output was suppressed, so `decision_alerts` was missing and the
  brief's "Theses under watch" section rendered empty while everything looked fine.
  Do not redirect migrate output.
