# Rust, US-market linkages, and options data — implementation decision

## Where Rust is justified

Keep Python for research design, PIT joins, DuckDB/Parquet I/O, model evaluation,
broker orchestration, and trader-facing contracts. Do not rewrite these merely to
change language: most time here is likely I/O or vectorized native-library work.

Benchmark the following *pure, bounded* kernels first, with realistic p50/p95/p99
latency, throughput, memory and identical-output tests:

| Candidate Rust kernel | Why | Move only if |
|---|---|---|
| Tick/order-book normalization and replay | High event rate, strict sequencing, bounded allocations | Python profiling shows this dominates a defined market-hours latency budget |
| Large option-surface/Greek batches | Repeated independent calculations across strikes/expiries | Existing NumPy/SciPy batch path misses a measured throughput target |
| Portfolio constraint/risk checks in an order gateway | Deterministic low-tail-latency checks | A separately authorized live path is designed and Python p99 violates its SLO |
| Massive parameter-search inner loop | Many repeated simulations | A fixed algorithm, output parity and representative workload show material gain |

Use PyO3 for an isolated local numeric kernel; an independently deployed execution
service would need a versioned IPC contract and fail-closed timeout behavior. Keep
the Python reference implementation and property tests. No Rust crate is added yet:
there is no recorded bottleneck or latency SLO that would justify it.

## US markets → Indian companies

`research/cross_market.py` accepts timestamped US proxy returns and *manually reviewed,
date-valid* company/domain exposures. It only joins a US observation that was available
before the Indian decision timestamp and within a bounded age. `IT_SERVICES`,
`PHARMA_EXPORTS`, `AUTO_SUPPLY_CHAIN`, `ENERGY_IMPORTS`, or other domains are hypotheses,
not automatic classifications. Mapping needs evidence, economic sign, exposure weight,
and history of revisions; a US index is a proxy, not a causal parent of every company
in a sector. The diagnostic reports descriptive correlations only after at least 30
pairs and does not authorize a signal.

An ingestion adapter still needs an authorized US data feed, adjustment policy,
holiday/calendar handling, timezone tests, FX and global-risk controls. Pre-register
each domain hypothesis, compare against India sector momentum, Nifty beta and USD/INR,
and test on untouched later sessions. Overnight US information is available by the
Indian open; a US close later that same Indian session is not. Corporate events and
earnings should be timestamped independently of price bars.

## Options data and rights

`fb options-audit` reports actual NSE F&O daily-option coverage separately from
two-sided quote-snapshot coverage; it never calls either source a historical bid/ask
fill dataset. Existing code has `ingest/nse_archives.py` (`fo_bhav`),
`ingest/kite_fno.py` (current chain/positioning), `ingest/options_quotes.py` (opt-in
append-only quote archive), and training-only observed-close option studies.
On this local checkout (2026-10-29), the read-only audit counted 122,389,495 daily
option rows across 2,914 sessions, 2015-01-01 through 2026-10-26, and no
`options_quotes.duckdb` archive. These counts do not establish bid/ask coverage,
fill quality, data rights, or profitable option strategies.

The next data work is: verify the owner's retention entitlement; archive the daily
instrument master before expiries; capture contract identity, bid/ask/depth, OI,
volume, source and receipt timestamps, quality flags and quote-age metrics; then
measure coverage by underlying/expiry/moneyness/session. For old expired options,
obtain an authorized historical source; do not synthesize missing historical spreads
from candles. Validate lot-size changes, corporate actions, expiry/settlement and
margin before any strategy-level paper result. Keep option buying, covered/defined-
risk spreads and naked selling as separately governed workflows.

Kite's [historical-data documentation](https://kite.trade/docs/connect/v3/historical/)
describes active-instrument candles and continuous *futures* day data; its
[developer support](https://kite.trade/forum/discussion/15820/how-to-fetch-historic-options-data)
states expired option candles are unavailable. US historical bars are available from
providers such as [Alpaca](https://docs.alpaca.markets/us/reference/stockbars), subject
to the actual plan, feed and use rights. These references describe capabilities, not
approval of Financial-Brain's retention or commercial use.
