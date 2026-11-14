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

CI now emits full-suite `coverage.json` and `critical-coverage.json` artifacts and enforces the six 60% per-domain floors on both Windows and Ubuntu. These conservative floors sit at least 22 percentage points below the measured full-suite baseline; they detect material regression without treating statement coverage as evidence of correctness or profitability.

The clean cross-platform acceptance run passed: [CI run #598](https://github.com/crakashp2905-hub/Financial-Brain/actions/runs/43601378017), commit `2c61abe6cbc50e2efa864fb8c883a734431894ef`. Both complete jobs succeeded, including full-suite coverage generation and the six-domain gate. The uploaded artifacts were `coverage-ubuntu-latest` (artifact 14116298799, SHA-256 `90fc80cbda4083fcd444167ad98dad934f6f02021d6656a5a022400ece6400a7`) and `coverage-windows-latest` (artifact 14116044939, SHA-256 `bee7a13047b899cfb5a91df277dd9a668b5544868d9cf942faa359249990c95b`).

High-priority uncovered modules from this measurement include decision custody, champion evaluation, archive and pretrade gates, Kite ingestion, and several risk utilities. A zero on this subset means no selected test imported that module; it does not imply production code is unreachable.

No profitability, live-market observation, or promotion claim follows from statement coverage.
