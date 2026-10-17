# Readiness scorecard

Written 2026-10-15 from the owner's 13-area "10/10 when" list. Each line has one status:

* **DONE**: true now, with the file that shows it.
* **PARTIAL**: some of it is true, and the gap is stated.
* **OPEN**: work that code can do and has not done yet.
* **TIME**: only calendar time can satisfy it. ADR-0004 G5 and the paper record cannot be bought with more modelling.
* **OWNER**: needs a human decision, account or signature. No agent may do it.

Nothing here is rated 10/10. The honest summary: the engineering and the research integrity are strong. The
forward evidence is weeks old. **No strategy is an edge yet**: M1 is a candidate with half-sample t of 1.45 and 1.24,
and C1 carry has full-sample t 2.40, below ADR-0006's 2.5. Nobody qualifies for the Rs 25,000 ticket tier, and the
bar will not be moved to change that.

## 1. Engineering and reproducibility

| criterion | status | evidence / gap |
|---|---|---|
| `pip install -e .[dev]` installs everything the tests import | DONE (2026-10-15) | `pyproject.toml`: numpy, pandas, scipy and scikit-learn are now core (they are imported at module level), and `dev` adds pytest, ruff, mypy, pip-audit, pypdf, pymupdf and lightgbm. torch is the only thing excluded; no test needs it |
| SciPy gap fixed | DONE | as above |
| clean Windows and Linux environments pass | DONE (2026-10-18) | Windows: a fresh venv from pyproject alone (it resolved pandas 3 / numpy 2.5) ran 1,428 tests: 1,423 passed and 5 failed (missing pyarrow, a pandas-3 None/NaN change, a wrapped wikilink, the torch test not skipping). All 5 were fixed and re-run green. CI's first run (2026-10-16): Linux 1,426 passed / 2 failed, Windows 1,427 / 1. The causes were a test pinning a stdlib robots.txt bug that newer Python fixed, and a **real bug**: on a machine up for less than the session TTL, the NSE session was never warmed (`monotonic()` counts from boot). Both fixed, with a regression test. Run 39061332514 (commit 3e018c7): **green on Windows and Linux** |
| CI on every commit: tests, lint, types, audit, migrations | DONE (config) / TIME (first run) | `.github/workflows/ci.yml`: ruff (repo-wide clean; 20 reviewed `noqa`, all closures called inside their own loop), mypy on the OMS, `standard.py` and `preregistry.py` (clean), pip-audit, `tests/test_migrations.py`, full pytest, Windows and Linux |
| no untracked source in a research result | DONE | `fundamental_tests.py` committed with its tests, both pre-registrations and both ledger rows |
| result = commit + data snapshot + params + seed + lockfile | DONE (2026-10-16) going forward; PARTIAL for older trials | `fb reproduce "<trial>"` fingerprints every input table (rows, last date, order-independent value hash), then runs the trial's script, now in the repo (`scripts/trials/`, all 13 trials of 2026Q4). `standard.record` stamps the commit and lockfile hash on every new trial. Manifests for X3, MF and I/S are in `docs/trials/manifests/`. The ten earlier trials have scripts but no fingerprint from their own run day: the data has refreshed since |
| high coverage and adversarial tests on critical modules | PARTIAL | OMS (`tests/test_oms.py`: timeouts, duplicates, partial fills, restarts, kill switch), standard, preregistry and ingest have tests. No coverage measurement yet (OPEN) |
| a failed step cannot yield a fresh-looking stale signal | DONE (2026-10-15) | `paper/terminal.freshness`: each input's last date against the expected session, with a red "STALE INPUTS - do not act" banner; tested |

## 2. Historical market data

| criterion | status | evidence / gap |
|---|---|---|
| raw files immutable, hashed, with URL and retrieval time | DONE | `lake/store.py`: sha256 per object, and a republished different file is kept beside the original |
| event, publication and as-of times | DONE for filings and results (`filed_at`, `disseminated_at`); PARTIAL elsewhere | |
| EOD coverage, corporate actions, ISIN successions | PARTIAL | `isin_successions`, `security_lineage`, `gap_reviews`; anomalies are classified via `fb gaps`. Not every demerger is reconciled |
| PIT investable universe | DONE | `edges.load`: the top 500 by trailing adv20 on each date, never today's list |
| historical index membership and TR benchmarks | PARTIAL | `index_constituents` is partial; `nifty_tr` exists for NIFTY 50 |
| DQ service-level targets and per-source contracts | DONE (2026-10-16) | `data_contracts.py` / `fb data-contracts`: for each of nine sources, the source, the time it carries, the revision policy, known limits and a maximum lag, measured live (exit code 1 when any fails). Duplicate and null-rate targets are not yet in it |

## 3. Point-in-time fundamentals

| criterion | status | evidence / gap |
|---|---|---|
| dissemination timestamps; restatements as new rows | DONE | `financial_results.filed_at`; a revision is a new filing |
| consolidated vs standalone explicit | DONE | first-reported, consolidated where filed, else standalone (`fundamental_tests.statements`) |
| XBRL reconciled to totals | DONE | `checks_passed`: 99.5% of Integrated Filing statements pass the arithmetic check |
| shares and market cap historically valid | DONE | paid-up capital ÷ face value from the same filing, times the unadjusted close (99.2% of statements) |
| 10+ years of history | **No: 2019-2026 only** (about 7 years) | earlier history would need a licensed PIT vendor (OWNER decision) |
| per-factor definition, lag, missing and survivorship policy | DONE for the two registered books | `brain/07-Research/PRE-REGISTERED multi-factor ...`, `... earnings surprise ...` |

## 4. Intraday and derivatives

| criterion | status | evidence / gap |
|---|---|---|
| minute history including delisted names | PARTIAL | 610 names, 2016-2026, from Kite. Kite has **no** delisted instruments, so intraday results carry survivorship bias, stated in each note |
| auctions, halts, circuits, special sessions | PARTIAL | a late open is skipped (`intraday_family`); circuits are not modelled |
| F&O history: expiries, OI, ban list | PARTIAL | `basis_contracts`, F&O snapshots from 2026-10; **no historical ban-list archive** |
| fills: delay, spread, impact, partial, lots, freeze | PARTIAL | the OMS slicer handles lots and freeze quantities; spread and impact are a flat cost by liquidity bucket; delay is modelled as the next bar |
| live stream reconciled to an archive | OPEN | |
| stale feed fails closed | DONE for orders | the risk gate refuses orders without a fresh LTP; the ban list being unreadable refuses every order |

## 5. Research integrity

| criterion | status | evidence |
|---|---|---|
| pre-registered before the decisive result, audited from git | DONE | `research/preregistry.py`. On 2026-10-15 it learned to tell a declared **origin** (a result the registration cites as its motivation, already committed) from a **result**; two new tests |
| mechanism, universe, horizon, costs, benchmark, null and gates named | DONE | every 2026Q4 registration |
| quarterly budget enforced; exceptions only by ADR amendment | DONE | `standard.record` refuses past budget. 2026Q4 is **13 of 13: spent**. No more trials before 2027-01-01 |
| negative results committed and searchable | DONE | `docs/trials/ledger.jsonl` (13 FAILs this quarter) and `fb kb` |
| family-level multiple-testing control | DONE | SPA, Reality Check and PBO on A10 (16 rules), K7 (17), B4 (9) and the I/S family (9) |
| train / select / validate / forward separation | DONE | combination agent: validation 2023-26 locked behind the budget (`validate()` raises) |
| fixed baselines | DONE | EW universe, random equal-turnover books (G4), and momentum, all named in each registration |

## 6. Alpha discovery and validation

No strategy clears all of it. **Status: TIME.** Today's standings:

| strategy | where it stands |
|---|---|
| M1 cost-aware momentum | candidate in the OOS monitor; half t 1.45 / 1.24; needs 12 post-registration rebalances (about 2027-10) |
| C1 rich-basis carry | full t 2.40 < 2.5; both halves positive; in the OOS monitor |
| everything else tested in 2026Q4 | FAIL (ledger) |

## 7. Portfolio construction and risk

| criterion | status | evidence / gap |
|---|---|---|
| cash as a first-class output | DONE (2026-10-15) | `decisions/risk_filters.cash_share`: the terminal states the cash share an equal-slot book would hold after the avoid rules |
| negative selection before picking winners | DONE (2026-10-15), display only | `decisions/risk_filters`: no momentum longs in CRISIS (IC t -8.4 / -11.1), avoid adv20 < 60% of its 1-year mean (observation tier, registered OOS), edge below cost, no edge. Frozen paper books are **not** changed (ADR-0004) |
| exposure by group, sector, factor, liquidity | PARTIAL | `portfolio/optimizer.py` (Ledoit-Wolf, sector caps); promoter-group exposure is not wired in |
| stress tests (2008, COVID, rate shock) | OPEN | |
| VaR, CVaR, liquidation time | PARTIAL | liquidation time via capacity (`opportunity_card.capacity_inr`); no CVaR report |
| every live limit has an owner, test, alert and fail-closed path | DONE for the OMS | `execution/oms/risk_gate.py`, `killswitch.py`, `docs/RUNBOOK-LIVE.md` |

## 8. Paper trading

| criterion | status |
|---|---|
| trades generated before outcomes; same timing and cost rules; frozen versions | DONE (`paper/`, zoo, live-v1 runner; rule changes become new registrations) |
| passed-over opportunities compared | DONE (`TV.watched_not_traded`, opportunity memory) |
| expected vs realised, MFE, MAE, slippage | PARTIAL (MFE/MAE from analogues; realised slippage needs fills) |
| sample long enough; an adverse regime observed | **TIME**: the October 2026 paper record is weeks old. Rated 2-3/10 as forward evidence, as the owner says |

## 9. Execution and OMS

| criterion | status |
|---|---|
| state machine, hash-chained journal, idempotent client ids, UNKNOWN never resent | DONE (`execution/oms/`, `tests/test_oms.py`) |
| reconciliation, kill switch, human-only approval, arming and reset | DONE (an `agent:` actor is refused) |
| 10 rehearsal sessions before TICKET; fills to measure slippage | **TIME / OWNER**: needs market-hours rehearsals with the owner's Kite session; none counted yet |

## 10. AI, models and agents

| criterion | status |
|---|---|
| models extract, classify and explain; no unsupported price prediction | DONE (System One chooses among typed options; deterministic gates decide) |
| prompts, models, latency and cost logged | DONE (`llm_calls`) |
| untrusted content cannot trigger tools | DONE by design (filing text is quoted as data in the terminal; the agent has no order path) |
| calibration on labelled Indian data | PARTIAL (tone classifier benchmarked; System One not calibrated) |
| LLM cannot bypass the risk, evidence or execution gates | DONE (OMS locks; `approve` refuses `agent:` actors) |

## 11. Trader workflow

| criterion | status |
|---|---|
| every opportunity states one of four claims | DONE (2026-10-15): `evaluation/evidence_tiers.claim_tier` (monotone: Historical observation < Replicated candidate < Forward-paper evidence < Confirmed strategy). The terminal list is labelled **Historical observation** (ranking v1 failed its walk-forward), shown muted, under "Not a trade recommendation"; each challenger is labelled too |
| why, why now, net alpha, base rate, case against, invalidation, plan, size, alternatives, cash | DONE (the per-name cards and the "before you buy" alternatives table), with cash now explicit |

## 12. Compliance and commercial readiness

**OWNER, all of it.** Personal use only (decided). Data rights are noted in `docs/FEASIBILITY-INDIA.md`. Several
things must happen before anything reaches another person:

* a SEBI RA/IA review;
* the exchange data licences;
* the broker's algo approval, static IP and identifiers, in writing;
* legal sign-off.

None has happened.

## 13. Operations and resilience

| criterion | status |
|---|---|
| scheduled jobs, retries, freshness | PARTIAL: Windows tasks and the terminal freshness banner; there are no retries or watchdog (the watchdog is an open owner decision since the 2026-10-14 refresh kill) |
| backups with restore drills | **OPEN / OWNER**: the lake is gitignored by design ("back it up separately"), and no backup job or drill exists |
| incident runbook | PARTIAL (`docs/RUNBOOK-LIVE.md` covers broker, OMS and kill switch; data failures and model compromise are not written) |
| secrets vaulted and rotated | PARTIAL (`.env` gitignored; no vault, no rotation) |
| one machine failure cannot lose evidence | **No**: a single Windows machine holds everything |

## What the next quarter is for

1. **Let time work:**
   * M1 and C1 accumulate out-of-sample months;
   * the insider feed accumulates (6-12 months before any test);
   * OMS rehearsals in market hours, with the owner.
2. **2027Q1 trials** go to new information only:
   * the value leg of the fundamentals as its own registration;
   * results-event drift by filing type;
   * rating actions and pledges as separate events;
   * the held-out drying-up volume replication already registered.
3. **Engineering:**
   * a research-DB snapshot hash per trial;
   * coverage measurement;
   * a backup job with a restore drill;
   * the refresh watchdog;
   * stress tests.

## The owner's second list (2026-10-16)

| item | status | evidence / gap |
|---|---|---|
| 1. a proven forward edge | **TIME** | the registered tests run on their own; the 2026Q4 budget is spent, so nothing new is added. See section 6 |
| 2. reproducible engineering | DONE / PARTIAL | dependencies, lockfile, CI, `fb reproduce` and data fingerprints (above). **Look-ahead checks:** `tests/test_fundamental_tests.py` (SUE uses only prior quarters), `test_phase5_preregistry.py` (order of registration). A general automated look-ahead scanner is OPEN |
| 3. data moat | PARTIAL / OWNER | **in the store:** results 2019-26, insider XBRL from 2026-07, delivery, announcements, ratings, pledges and order wins as separate event types, contracts (`fb data-contracts`). **OPEN (code):** bulk/block deals, shareholding patterns, historical ban list. **OWNER (licensed):** consensus estimates and revisions, delisted-name intraday history, full historical index constituents |
| 4. trader workstation | PARTIAL | the terminal has: the ranked list with its evidence claim; explicit "no trade" (the risk filters and the cash share); catalyst, evidence, regime, net alpha, downside, liquidity and invalidation per name; **new** a calendar (board and results meetings parsed from intimations, ex-dates, F&O expiries); **new** an order-ticket preview with the real risk gate's verdict; post-trade review (`fb journal`). Exposure by sector is shown; by factor and failure mode it is not (OPEN). Macro calendar not collected |
| 5. execution calibration | DONE (tooling) / **TIME** (evidence) | `fb live quality`: fill rate, partials, rejections, slippage against arrival, latency and the gap to the modelled cost, by liquidity bucket, live and rehearsal kept apart. There are no fills yet. Contract-note reconciliation is OPEN (PDF parsing). Kill-switch drills are in `tests/test_oms.py`; live drills need the owner |
| 6. portfolio construction | PARTIAL | sizing by volatility, liquidity participation, weight cap and risk per trade (`decisions/plan.py`); cash explicit; capacity per name; **new** `fb stress`: 2016, 2018, 2020, 2022 and 2024 replayed on the held names, market -20% through beta, sector rotation, and liquidation time at 10% of a halved ADV. On the owner's book today COVID would have cost 57%. Not modelled: INR shock (no FX series), factor caps |
| 7. personal learning loop | DONE (2026-10-16), waiting on trades | `fb journal --import <Console tradebook.csv>`; detectors for overtrading, averaging down, premature exits, event chasing and ignored invalidations (`portfolio/behaviour_review.py`); comparison with the day's approved list (`evaluation/regret.journal_vs_list`); an anti-playbook (a habit with at least 8 trips, losing at t <= -2, printed with how many habits were examined). The journal is empty today: Kite keeps one day of trades, so an export import is the way to fill it |
