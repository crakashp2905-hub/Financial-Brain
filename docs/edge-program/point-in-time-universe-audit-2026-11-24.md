# Point-in-time universe audit — 2026-11-24

Checklist item: PA-02-09 — keep delisted/suspended equities, expired option tokens, historical scheme closures, futures rolls, and ETF listing changes in their past universes.

## Existing support

- The kernel instrument registry resolves dated symbol aliases and status history by the requested date. Missing historical status defaults to DELISTED, and only ACTIVE/LISTED instruments pass the tradability gate.
- The source-aware instrument master preserves effective-dated provider tokens/identities and rejects overlapping identity ranges. Exact option resolution refuses expired contracts and absent listed strikes.
- ETF reference lookup is date-keyed and chooses the latest source observation no later than the requested date.
- Evidence: tests/test_forward_edge_instrument_master.py, tests/test_kernel_instruments.py, and tests/test_etf_feed.py (53 passed).

## Remaining limits

Status remains PARTIAL. The contracts and point-in-time lookup behavior exist, but no complete source-backed historical universe is loaded for delisted/suspended equities, expired option symbols, futures rolls, closed MF schemes, or ETF listings. There is no completeness reconciliation against exchange/AMFI lifecycle files, and provider rights for retained history remain unverified. The current tests demonstrate lookup behavior on fixtures, not universe completeness.
