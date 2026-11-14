# PE-0196 critical coverage baseline

## Measurement

Coverage was measured with coverage.py over 193 focused OMS, accounting, custody, market-data, evaluation and risk tests. This is a diagnostic subset, not the full CI suite and not a valid basis for setting final enforcement floors.

| Domain | Statements covered | Focused coverage |
| --- | ---: | ---: |
| OMS | 730 / 902 | 80.93% |
| Custody | 129 / 365 | 35.34% |
| Market data | 281 / 591 | 47.55% |
| Accounting | 504 / 1,133 | 44.48% |
| Evaluation | 376 / 981 | 38.33% |
| Risk | 638 / 1,236 | 51.62% |

The 60% candidate floor fails five domains on this subset. It is intentionally not wired as a merge gate yet: doing so would make CI red without providing a measured full-suite baseline. Keep the report diagnostic until full-suite coverage is collected on clean Linux and Windows runs. Then set approved per-domain floors from the baseline and raise them incrementally with focused tests.

High-priority uncovered modules from this measurement include decision custody, champion evaluation, archive and pretrade gates, Kite ingestion, and several risk utilities. A zero on this subset means no selected test imported that module; it does not imply production code is unreachable.

No profitability, live-market observation, or promotion claim follows from statement coverage.
