# Future source mutation and look-ahead audit — 2026-11-24

Checklist item: `PA-02-15` — mutate source rows published after the decision and require byte-identical features and intended orders.

## Verified

- Added `tests/test_forward_edge_future_source_mutation.py` using two independent `QuoteArchive` databases with identical point-in-time inputs.
- Each database receives two additional source rows whose event timestamps are at or before the decision, while their publication, receipt, and strategy-availability clocks occur after the decision. The second database changes those late rows to extreme prices.
- The test obtains the production point-in-time snapshot, derives the moving-average feature with `market_data.feature_at`, converts the resulting side/price/quantity into the canonical `execution.intent_from_signal` order intent, and compares the complete serialized feature+intent bytes.
- Both evaluations are byte-identical. The archived snapshot therefore excludes late-published revisions even when their exchange/event timestamps look historical.
- Verification: `python -m pytest tests/test_forward_edge_future_source_mutation.py -q` — 1 passed; `python -m ruff check tests/test_forward_edge_future_source_mutation.py` — passed; `python -m mypy tests/test_forward_edge_future_source_mutation.py` — passed.

## Remaining limits

Status remains `PARTIAL`. This is a focused point-in-time archive, feature, and canonical order-adapter check. The full catalogue of production strategy implementations and external data pipelines has not been run through an automated source-publication mutation matrix, so it does not prove every strategy is free of look-ahead.
