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

The 60% candidate floor fails five domains on this focused subset, so the subset was not a sufficient basis for enforcement. A full Windows local run on 2026-11-14 completed 4,085 tests with one opt-in test skipped and measured these domain totals:

| Domain | Statements covered | Full-suite coverage |
| --- | ---: | ---: |
| OMS | 756 / 902 | 83.81% |
| Custody | 328 / 365 | 89.86% |
| Market data | 547 / 603 | 90.71% |
| Accounting | 931 / 1,133 | 82.17% |
| Evaluation | 870 / 981 | 88.69% |
| Risk | 1,032 / 1,236 | 83.50% |

CI now emits full-suite `coverage.json` and `critical-coverage.json` artifacts and enforces the six 60% per-domain floors on both Windows and Ubuntu. These conservative floors sit at least 22 percentage points below the measured full-suite baseline; they detect material regression without treating statement coverage as evidence of correctness or profitability. The first clean cross-platform gate run remains the acceptance check for this change.

High-priority uncovered modules from this measurement include decision custody, champion evaluation, archive and pretrade gates, Kite ingestion, and several risk utilities. A zero on this subset means no selected test imported that module; it does not imply production code is unreachable.

No profitability, live-market observation, or promotion claim follows from statement coverage.
