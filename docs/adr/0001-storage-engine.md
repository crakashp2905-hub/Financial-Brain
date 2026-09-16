# ADR-0001 — DuckDB now, PostgreSQL + TimescaleDB later

**Status:** accepted · **Date:** 2026-09-16 · **Phase:** 0

## Context

[ARCHITECTURE.md](../ARCHITECTURE.md) and [BUILD-FLOW.md](../BUILD-FLOW.md) name
PostgreSQL + TimescaleDB as the warehouse. That is the right production target. But
Phase 0's whole purpose is to *start accumulating point-in-time data immediately* — see
[C03](../BUILD-FLOW.md), where every month of delay is a month of data that cannot be
bought back — and standing up Postgres + Timescale is a setup step between the decision
and the first ingested row.

## Decision

Phase 0 ships on **DuckDB**, embedded, with **all curated market data written as
Parquet**.

- Curated prices: `data/curated/eod_prices/exchange=*/business_date=*/part-0.parquet`
- Reference, provenance and PIT tables: inside `data/financial_brain.duckdb`
- Raw payloads: plain files in `data/lake/`, keyed exactly as an object-store key

Nothing uses DuckDB-specific SQL beyond the Parquet views in
`storage/db.py::_refresh_views`.

## Why this is reversible

The asset being accumulated is **the data, not the engine**. Parquet is
engine-independent; the lake is plain bytes plus JSON sidecars; the schema in
`storage/schema.sql` is ordinary SQL. Migration is therefore a load script:

1. `CREATE TABLE` from the same `schema.sql` (Timescale hypertable on `eod_prices`)
2. `COPY` the Parquet partitions in
3. Replace `Database` with a psycopg implementation behind the same small surface
   (`connect`, `query`, `query_dicts`, `execute`)

No business logic changes, because every module takes a connection rather than importing
a driver.

## Consequences

**Good**
- Zero infrastructure. `pip install -e .` then `fb ingest` — nothing to provision.
- Parquet + DuckDB is genuinely fast for this shape of work (columnar scans over daily
  partitions).
- The lake-to-object-store path is already correct; only the prefix changes.

**Bad — and one of these has already bitten**
- **DuckDB is single-writer.** A long ingest holds an exclusive lock, so read commands
  fail while it runs. Hit during the first backfill. Mitigations now: jobs hold *one*
  connection for a whole range (`run_range`), and read commands report the lock plainly
  instead of raising. Mitigation later: Postgres removes the limitation entirely.
- No concurrent multi-process access, so no parallel ingestion across exchanges.
- No Timescale continuous aggregates or retention policies yet.

**Migrate when any of these becomes true**
- More than one process needs to write
- Ingest and query genuinely need to run concurrently
- The PIT store exceeds comfortable single-file size (tens of GB)
- A service needs network access to the warehouse

## Dependency note

`pytz` is a required dependency, not an accident: DuckDB needs it to convert
`TIMESTAMPTZ` values into Python objects, and every timestamp in the PIT store is
timezone-aware by design (the four-timestamps rule).
