# Phase 0 — Foundations (delivered)

**Goal:** a trustworthy Indian market dataset. No AI in this phase, by design.
**Exit test:** reproduce any past day's investable universe, adjusted prices and
known-at-the-time fundamentals from cold storage.

```
$ fb check --on 2026-09-11
[PASS] universe reconstructable      8653 instruments
[PASS] ISIN identity resolvable      5902 ISINs
[PASS] raw payload preserved            2 lake objects
[PASS] quality contracts held           0 errors
PASS - the day is reconstructable from cold storage.
```

## What exists

| # | Component | Module | State |
|---|---|---|---|
| C00 | Immutable raw lake | `lake/store.py` | done |
| C01 | Security master (ISIN-keyed) | `securities/master.py` | done |
| C02 | Data provider abstraction | `providers/base.py` | done — NSE + BSE implemented |
| C03 | PIT observation store | `pit/observations.py` | done — **accumulating from today** |
| C04 | Universe snapshots | `universe/snapshot.py` | done |
| C05 | Indian cost model | `costs/india.py` | done |
| — | Ingestion jobs, DQ contracts, lineage | `ingest/` | done |
| C26 | Corporate actions + adjustments | `corpactions/actions.py`, `detect.py` | done — derived from Tier-1 data, two detectors |
| C27 | Benchmark / index history | `providers/reference.py` | done — 66 days, ~165 indices |
| C28 | Reference data + F&O eligibility | `providers/reference.py` | done — listing dates, short-ability flag |
| — | Phase 0 completion gate | `cli.py::cmd_gate` | done — 12 bases, exits non-zero on failure |

56 tests, all passing, all offline — the network is not a test dependency.

## Current holdings

```
raw lake            404 files, 67.9 MB
lake manifest       130 objects
securities (ISIN)   8,689
listings            14,013
universe rows       550,213
coverage            2026-06-15 .. 2026-09-15  (65 trading days, NSE + BSE)
```

## What the first real backfill discovered

Running 65 trading days across both exchanges surfaced four things no amount of design
work would have:

**1. UDiFF is identical on both exchanges, and carries ISIN natively.**
Same 34 columns, same semantics, `ISIN` present on every row. Canonical identity is a
parse away rather than the reconciliation project the design assumed. `FinInstrmId`
additionally gives the BSE scrip code and NSE token for free.

**2. BSE published an impossible row.** On 2026-07-29, `Maple Infrastructure Trust`
(INE0M5S23019) came through with open = high = low = 144.00 and **close = 142.50** — a
close outside the day's own range. The quality gate caught it.

This forced a design change. The original gate was all-or-nothing, so one bad row
discarded 4,929 good ones. Contracts now have **two scopes**:

- **file scope** — wrong date, too few rows, duplicate instruments → publish nothing
- **row scope** — impossible OHLC, bad ISIN, negative volume → reject *those rows*,
  publish the rest, and keep the rejects in `rejected_rows` with a reason

That date now ingests as `ok_partial`, 4,844 rows published, 1 rejected. There is a
regression test named after it.

**3. BSE signals "no data" with an HTML page, not a 404.** NSE returns 404 on holidays;
BSE serves its SPA landing page with HTTP 200. Both 2026-06-26 and 2026-09-14 were
non-trading days that NSE correctly 404'd. Treating an HTML body as `not_published` turned
two false failures into correct holiday handling.

**4. DuckDB's single-writer lock is real.** Opening a connection per pipeline step caused
intermittent lock failures mid-backfill. Jobs now hold one connection for a whole date
range (`run_range`). Documented in [ADR-0001](adr/0001-storage-engine.md), along with the
remaining limitation: read commands cannot run while an ingest holds the lock.

## Findings the data itself produced

**Survivorship is not hypothetical.** Over 65 trading days on NSE:

```
at start   3,420 instruments
at end     3,692
vanished     182   ← invisible to any current-universe backtest
appeared     454
```

**ISIN-primary was the right call.** In 65 days, **125 ISINs appeared under more than one
ticker** (e.g. `813CG2045A → 813GS2045`), and **3,304 ISINs trade on both exchanges**. A
ticker-keyed master would have silently split or merged those.

**The cost model is punitive, as it should be.** Mid-cap bucket, ₹1 lakh notional:

```
ROUND TRIP  Rs 708.75   70.9 bps
breakeven move needed: 0.709%

Annual drag by turnover:
    1x    0.71%
    4x    2.83%
   12x    8.50%     ← monthly rebalancing costs 8.5% a year before any alpha
   52x   36.85%
```

That 8.50% is the number that will kill most Indian factor strategies, and it is now
computable *before* a backtest rather than discovered after. It feeds the
India Implementability Gate (C19) directly.

## Design decisions made here

- **[ADR-0001](adr/0001-storage-engine.md)** — DuckDB + Parquet now, PostgreSQL +
  TimescaleDB later. Curated data is Parquet, so the migration is a load script, not a
  rewrite. The asset is the data, not the engine.
- **Prices stored unadjusted, adjusted on read.** An adjusted price is a derived opinion
  that changes whenever a new action lands; the traded price is a fact. Storing the fact
  means a late-discovered corporate action re-adjusts history correctly instead of
  corrupting it.
- **Row-scope vs file-scope quality contracts** — forced by real BSE data, above.
- **Providers return bytes, not parsed objects.** The lake stores exactly what the source
  served (NSE's zip stays a zip), so parsing is always replayable and always auditable.

## How to use it

```bash
fb migrate                                   # create/upgrade the schema
fb ingest --start 2026-06-15 --end 2026-09-15   # both exchanges
fb ingest --start 2026-09-11 --from-lake     # rebuild from stored bytes, no network
fb status                                    # what the system holds
fb runs --failed                             # what needs attention
fb dq                                        # failing quality checks
fb check --on 2026-09-11                     # the exit test
fb resolve TCS --on 2026-07-01                # ISIN identity, point-in-time
fb universe --on 2026-09-11 --exchange NSE   # the universe as it was
fb universe --churn-from 2026-06-16 --on 2026-09-15   # survivorship, counted
fb costs --turnover 100000 --bucket mid      # what trading actually costs
fb actions --suspicious                      # unexplained gaps = missing corporate actions
fb reference                                 # sync master list + F&O eligibility
fb index --start 2026-06-15 --end 2026-09-16 # benchmark history
fb derive                                    # derive corporate actions from Tier-1 data
fb gaps                                      # triage unexplained overnight moves
fb gate                                      # can Phase 1 start?
```

## Phase 0 completion gate

Phase 1 builds perception on this data, so every fault here is inherited by every brief,
thesis and backtest built later. `fb gate` refuses to bless Phase 1 until each base holds:

```
$ fb gate
PHASE 0 COMPLETION GATE
==========================================================================
[PASS] price history        65 trading days
[PASS] both exchanges       65/65 days have NSE and BSE
[PASS] security master      8689 ISINs
[PASS] listing dates        2577 securities with listing dates
[PASS] short-ability flag   210 F&O-eligible names
[PASS] benchmark history    66/65 days of index levels
[PASS] corporate actions    24 actions, 22 adjustment factors
[PASS] gaps all triaged     0 untriaged, 7 reviewed
[PASS] raw lake             198 immutable payloads
[PASS] lineage intact       0 runs cite a missing lake object
[PASS] no unresolved dates  0 (source, date) pairs never succeeded
[PASS] PIT store live       append-only store present
==========================================================================
All 12 bases complete. Phase 1 may begin.
```

It exits non-zero when a base fails, so it works as a scheduled guard rather than a
one-off ceremony. Writing it immediately caught two faults in itself — it was counting
*historical* failures rather than currently-unresolved dates, and it demanded zero price
gaps when a stock can genuinely move 40% overnight.

---

## Closing the gaps

### Corporate action history — closed, by derivation
NSE's corporate-action API sits behind `www.nseindia.com`, which returns **HTTP 403** to
non-browser clients. Rather than build a scraper that breaks on their next bot-detection
change, actions are derived from Tier-1 data we already hold — via two detectors, because
the first one alone turned out to be insufficient.

**A. Restated previous close.** For some action types the exchange restates
`PrvsClsgPric` on the ex-date, making `stated_prev / actual_prev` the adjustment factor
straight from the exchange. 116 candidates over 66 days, **17 corroborated**.

**B. Close-to-close gap.** Testing A against real splits showed NSE **does not restate**
for ordinary splits and bonuses — ZFCVINDIA (2026-06-24), GOODLUCK (2026-08-21) and PGIL
(2026-09-11) all carried the raw unadjusted previous close and simply gapped. Detector A
was blind to every one of them. Detector B reads the gap and snaps it to the nearest
plausible ratio.

Result: **24 corporate actions, 22 adjustment factors**, including
`ZFCVINDIA 1:6`, `GOODLUCK 1:3`, `TRIVENI 1:2`, `BRIGADE 3:4`, `GENESYS 2:3`,
`HEXAGON 4:3`, `PGIL 1:2`.

Three safeguards, each forced by real data:

- **Series contamination.** TCS carried a block-deal (`BL`) row with `prev_close` 3019.00
  against a clean `EQ` 2059.60 — comparing across series invented a 1.47× "action" for
  TCS. Comparisons now match on series.
- **Invented ratios.** A generic closest-fraction search called 0.8383 a "16:19 split" at
  0.45% error. Only ratios real Indian actions use are accepted (`min ≤ 3`, `max ≤ 25`),
  so "12:7" can no longer be produced.
- **Corroboration by default.** 116 restated-prev candidates graded to only 17
  corroborated; the remaining 99 were almost entirely illiquid BSE `XT`/`T` names whose
  stated previous close wanders for reasons unrelated to corporate actions. Recording
  those would inject false adjustment factors — silently corrupting price history, which
  is worse than having none. A single-exchange gap is recorded **only** when the ratio
  snaps within 1%, because there the ratio must carry the evidence alone (PGIL at 0.13%
  qualifies; ESCONET's +87% rally, 6.7% from 2:1, does not).

**Honest limit:** this detects *that* an adjustment happened and by how much. It cannot
distinguish a 1:1 bonus from a 1:2 split, cannot see actions that do not move the
reference price, and needs the prior session in the lake. It is a strong Tier-1
substitute for a corporate-action feed, not a replacement for one.

### Unexplained gaps — triaged, not hidden
A 40% overnight move is either a corporate action or a real move, and no amount of
engineering makes that distinguishable in every case. So the standard is not "no gaps"
but **"no gap left unexamined"**: `fb gaps` lists untriaged ones with their nearest ratio
and snap error, and `fb gaps --review` records a verdict.

Seven remain, all marked `needs_source`. Every one is **single-listed**, so
cross-exchange corroboration is structurally impossible, and none snaps tightly enough to
record on ratio evidence alone (best: TIRUPATIFL at 1.5% from 3:2). That verdict is the
truthful one — it makes the residual dependency on a real corporate-action feed explicit
rather than papering over it with a guess.

### Benchmark history — closed
`ind_close_all_DDMMYYYY.csv` gives ~165 NSE indices per day with OHLC **plus P/E, P/B and
dividend yield** — a valuation-context bonus that will matter to the Market Regime Brain.
**66 days ingested, 2 non-trading days correctly skipped.**

Constituent history remains unsourced: NSE does not publish "NIFTY 500 membership as of
date X" in clean form. Levels are enough to benchmark returns; constituents are needed to
reconstruct index-relative universes.

### Reference data and short-ability — closed
`EQUITY_L.csv` gives **2,577 NSE securities with listing dates**, which bound how far back
a name could belong to any universe. `fo_mktlots.csv` gives **210 F&O-eligible names**
resolved to ISIN (6 unresolved are index futures like NIFTY and FINNIFTY, which correctly
have no ISIN).

F&O eligibility is the flag that matters most: Indian cash equities cannot be shorted
beyond intraday and SLB is thin, so "is there a future on this?" is the binary
short-ability test the India Implementability Gate needs.

### `fb pit --history` — fixed
It passed `con and entity` instead of the entity key. Now returns the restatement trail,
with a regression test.

---

## What is still genuinely missing

1. **An authoritative corporate-action feed.** Derivation covers corroborated and
   tightly-snapping cases; seven triaged gaps are marked `needs_source` precisely because
   they cannot be resolved without one. Candidates: a paid feed, or a browser-session
   fetch of NSE's API run as a separate attended job.
2. **ASM / GSM / T2T surveillance flags.** No archive-host endpoint; they sit behind the
   403-gated API. `security_flags` therefore carries F&O eligibility only. These affect
   margins and position limits rather than the binary can-we-trade-it question, so the
   most important gate input is covered.
3. **Index constituent history.** Not freely published in clean form.
4. **Depth of history.** 65 trading days. The archives go back much further; backfilling
   is a long-running job, not a design question, and it should run before Phase 3 needs
   regime variety.
5. **Reads block during ingest** (ADR-0001). Acceptable for a scheduled single-writer
   job; removed by the PostgreSQL migration.

## Next

Phase 1 (Perception): event intelligence from NSE/BSE announcements, the Market Regime
Brain from FII/DII + VIX + breadth, versioned world state, evidence ledger, and the daily
"what changed since yesterday?" brief. See [BUILD-FLOW.md](BUILD-FLOW.md).

Two things remain worth doing early because they only get more expensive:
**backfill the lake as far as the archives go**, and **acquire an authoritative
corporate-action feed** to resolve the seven `needs_source` gaps and everything like them
in deeper history.
