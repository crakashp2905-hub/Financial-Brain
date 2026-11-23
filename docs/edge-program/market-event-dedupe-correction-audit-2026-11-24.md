# Market-event deduplication and correction audit — 2026-11-24

Checklist item: `PA-02-06` — deduplicate quote and candle records using source identity while retaining corrections and late arrivals.

## Implemented

- `MarketEvent.dedupe_sha256()` binds event ID, event kind, venue, instrument, source, sequence, source clock, canonical payload hash, and optional correction parent. It excludes local receipt and strategy-availability clocks so a redelivered source record is idempotent.
- `QuoteArchive` now stores a source-identity record key instead of enforcing global event-ID uniqueness. The schema migration carries forward the existing event JSON, full event hash, and known ingest time. It does not invent ingest times for legacy archives.
- Same source identity and payload is ignored on repeat delivery. A reused event ID with a distinct source identity or payload is retained as a separate immutable row; ambiguous event-ID-only reads fail closed.
- Corrections append a new event carrying `revision_of`. The parent must already exist with the same source, venue, and instrument. The original record is never overwritten.
- Older source-clock records remain archived; they are not dropped because they arrive late.

## Verification

- `python -m pytest tests/test_forward_edge_market_events.py tests/test_forward_edge_quote_archive.py -q`: 29 passed.
- Broader `tests/test_forward_edge_session_runner.py`, `tests/test_phase5_intraday_runner.py`, and `tests/test_forward_edge_pipeline_compat.py`: 90 passed; two unrelated intraday tests initially hit the Windows sandbox's long temp-path limit, then both passed when rerun with a short in-worktree pytest temp directory.
- `python -m ruff check` on the changed implementation and focused tests: passed.
- `python -m mypy src/financial_brain/forward_edge/market_events.py src/financial_brain/forward_edge/quote_archive.py`: passed.
- Schema migration coverage confirms legacy rows survive and unknown historical ingest time remains `NULL`.
- Quote and candle fixtures cover duplicate delivery, source-identity separation, correction append, ambiguous event-ID lookup, and late source-clock preservation.

## Remaining limits

Status remains `PARTIAL`. No authorized exchange/provider correction feed or representative production archive was available to prove upstream revision identifiers and real-world source-clock quality. This implementation preserves an explicit `revision_of` chain when supplied; it cannot infer that an arbitrary changed payload is a provider correction. Provider permission and source authenticity are separate gates and remain unresolved.
