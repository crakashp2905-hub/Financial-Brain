# FourNode_India_v2: preregistration (written 2026-11-18, before any v2 run)

v2 is a **clone** of Four-Node. The original strategy, its spec, its 128-variant engine and its results
are not changed (`strategies/four_node/` is untouched). v2 reuses the original node and indicator
code and changes only what is listed below. Each change targets the diagnosis of the v1 failure: a
risk unit of one tick beyond N3 is smaller than the cost of a round trip.

## Fixed before running

| Item | v1 | v2 | Why |
|---|---|---|---|
| Bars | 5-minute | **15-minute**, built from the same 5-minute bars (25 per session) | larger moves per trade relative to a fixed cost |
| Interpretation | 128 variants | **one**: V0001000 (choices 1-3 as the source text says, as the owner answered on 2026-11-18; choices 4-7 from the best 2016-2021 v1 variant with that prefix) | chosen on discovery data only |
| Stop | N3 ± tick | **farther of** N3 ± tick and entry ∓ 1.0 ATR(14) | stop out of the bar's noise |
| Max risk | 1.5 ATR | 2.5 ATR | allows for the wider stop |
| Min risk | none | **0.5% of price** | keeps costs to 0.28 R or less |
| Target | 2.0 ATR | **2.0 R** | the target scales with the stop |
| Trail | at +1 ATR, remove TP, lock in steps of 0.5 ATR | at +1 R lock +0.5 R, then steps of 0.5 R; **TP stays** | protects winners without cutting the 2 R target |
| Barrier clearance | 2 ATR, barriers ahead only | 1 ATR, barriers ahead only | 15-minute ATR is larger than 5-minute ATR |
| D1 / D2 | 1.5 / 1.0 ATR | same multiples, measured on 15-minute ATR | |
| Positions | one per N1-N2-N3 triplet | **one open position per stock** | no stacking |
| Entry window | 14:50 cut-off, 15:15 flatten | 14:30 cut-off, 15:15 flatten | |
| Costs | CostModel intraday, mega bucket | same | |

## Periods and pass bar

- DISCOVERY 2016-2021, VALIDATION 2022-2023, TEST 2024 onward. v2 has never been run on any of them.
- The run happens once. No parameter is changed after the results are seen; any change is a new trial (v3).
- **PASS** requires all four of:
  1. mean net per trade > 0 in DISCOVERY;
  2. mean net > 0 in VALIDATION with t ≥ 2;
  3. mean net > 0 in TEST with t ≥ 2;
  4. at least 100 trades in each period.
- Anything else is FAIL. A PASS makes v2 a SHADOW_ONLY paper candidate. It does not promote v2. Promotion
  needs the ADR-0007 bar and forward paper sessions, and ADR-0007 is not changed for this.
- The look is logged in `docs/trials/dynamic/ledger.jsonl` as a family of 1.
