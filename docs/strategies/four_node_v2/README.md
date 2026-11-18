# FourNode_India_v2: a clone of Four-Node

The original Four-Node (`strategies/four_node/`) is unchanged. v2 is a separate strategy that reuses
its node and indicator code, preregistered in [PREREGISTRATION.md](PREREGISTRATION.md)
(sha256 `d3975c34…`, written 2026-11-18 14:31 UTC, before the run).

## Result of the single preregistered run: FAIL

| Period | Trades | Gross bps/trade | Net bps/trade | t | Win rate | Mean R | Exits (time / stop / target) |
|---|---|---|---|---|---|---|---|
| Discovery 2016–2021 | 294 | +11.7 | −2.3 | −0.37 | 43% | +0.09 | 137 / 120 / 37 |
| Validation 2022–2023 | 132 | +11.9 | −2.1 | −0.26 | 45% | +0.08 | 73 / 46 / 13 |
| Test 2024– | 280 | +6.0 | −8.0 | −1.68 | 43% | +0.10 | 156 / 94 / 30 |

Costs are a 14.0 bps round trip (`CostModel`, intraday, mega bucket). Only the minimum-trades
gate passed. Raw output: [results/run.json](results/run.json).

## Compared with v1

| | v1, best of 128 (5-minute bars) | v2 (15-minute bars, wider stop) |
|---|---|---|
| Gross bps/trade, discovery | +3 to +9 | +11.7 |
| Net bps/trade, discovery | −17 to −21 (top variants) | −2.3 |
| Net, validation | −10 to −12 | −2.1 |

The wider, cost-aware stop roughly doubles the gross edge. It is still smaller than the round-trip
cost, and the test period is weaker than the first two periods. About half the trades end at the
15:15 flatten without reaching the 2 R target, so the setup does not carry far enough within one
session.

## Status

Research only. v2 is not a paper candidate. Under the preregistration, any further change is a new
trial (v3) with its own registration and its own entry in the dynamic ledger.
