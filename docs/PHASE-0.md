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
| — | Corporate actions + adjustments | `corpactions/actions.py` | schema + engine done, **history not yet sourced** |
| — | Index/benchmark history | `storage/schema.sql` | schema only, **not yet sourced** |

34 tests, all passing, all offline — the network is not a test dependency.

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
```

## Known gaps, carried forward

1. **Corporate action history is not sourced.** The schema, adjustment engine and
   suspicious-gap detector all work and are tested, but no CA feed is wired up. Until it
   is, `fb actions --suspicious` is the stand-in — it finds overnight moves ≥20% with no
   recorded action, which is how splits and bonuses announce themselves in price data.
2. **Index and benchmark history is schema-only.** NIFTY constituent history is not freely
   published in clean form; it has to be accumulated or bought.
3. **Surveillance flags (ASM/GSM/T2T) are not sourced.** `security_flags` exists and is
   unused. These change tradability and belong in the Implementability Gate.
4. **`fb pit --history` has a bug** in argument handling; the store and its tests are
   fine, only that CLI path is wrong.
5. **Reads block during ingest** (ADR-0001). Acceptable for a scheduled single-writer job;
   removed by the Postgres migration.
6. **Only 65 days ingested.** The lake should be backfilled as far as the archives allow —
   that is a long-running job, not a design question.

## Next

Phase 1 (Perception): event intelligence from NSE/BSE announcements, the Market Regime
Brain from FII/DII + VIX + breadth, versioned world state, evidence ledger, and the daily
"what changed since yesterday?" brief. See [BUILD-FLOW.md](BUILD-FLOW.md).

Before that, two things are worth doing because they only get more expensive:
**backfill the lake as far as the archives go**, and **wire a corporate-action source**.
