# Market-fact timestamp contract audit — 2026-11-24

Checklist item: `PA-02-07` — preserve exchange, receipt, availability, publication, and computation clocks across relevant feeds, and prevent unavailable facts from reaching a signal.

## Implemented

- `kernel.clock.MarketFactTimes` already represented optional source/exchange/publication clocks plus required receipt, strategy-availability, and ingestion clocks.
- Added optional `computed_at`, normalize it to UTC with the other provenance instants, and reject a record whose strategy availability precedes computation.
- Existing `assert_usable_at(decision_time)` continues to reject facts whose availability exceeds the decision time.

## Verification

- `python -m pytest tests/test_kernel_units_clock.py tests/test_kernel_instruments.py -q`: 77 passed.
- Scoped Ruff and mypy passed for the clock contract and its tests.
- Repository search found no production adapter constructing `MarketFactTimes`; its current direct call sites are tests. Therefore the shared contract does not establish real feed adoption.

## Remaining limits

Status remains `PARTIAL`. Relevant Kite, exchange, ETF, MF, news, corporate-action, and derived-feature adapters have not all been mapped to this contract or proven to preserve all five clocks. Feed-by-feed causality checks and an end-to-end guarantee that only available facts reach every signal remain open.
