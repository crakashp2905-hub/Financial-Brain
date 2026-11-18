# Four-Node Fractal / 20C — research specification

Research and paper only. Not a verified profitable strategy. Previous failed intraday trials stand.

## Sources (verbatim, checksummed in `source/SHA256SUMS`)
- `original_rules_2025-08-22.txt` — the user-supplied XAUUSD M1 rules.
- `xauusd_4node_reference.pine` — a Pine Script interpretation. It resolves the ambiguities by
  mirroring the sell rules; that is **one** interpretation, recorded as the `MIRROR_*` alternatives,
  not adopted.

## Variants
| Variant | Market | Executable | Units |
|---|---|---|---|
| `FourNode_XAU_Original_v1` | XAUUSD | never (reference) | source pips (0.10) |
| `FourNode_India_v1` | Indian index/stock/ETF | only after every ambiguity has an owner-confirmed choice | ATR multiples |

## Owner decisions required (signals fail closed until recorded)
| Id | Question | Alternatives |
|---|---|---|
| BUY_EXCEPTION_SIDE | Buy exception: close below or above N2? | LITERAL_BELOW, MIRROR_ABOVE |
| BUY_N3_MID | Buy N3 vs 20C mid | LITERAL_LE_MID, MIRROR_GE_MID |
| N2_BUY_COMPARATOR | "N2 ≤ 20H High" for buy | LITERAL_LE_HIGH, MIRROR_GE_HIGH |
| N4_DEFINITION | What is N4? | NEXT_OPPOSITE_FRACTAL, BREAKOUT_CANDLE |
| SIGNIFICANT_CANDLE | Wick rule | BOTH_WICKS_GT_BODY, COMBINED_WICK_GT_BODY |
| FIB_ANCHOR | Daily Fibonacci direction (previous completed day only) | PREV_DAY_LOW_TO_HIGH, PREV_DAY_HIGH_TO_LOW |
| RETURN_CANDLE | "Without a return candle" | NO_OPPOSITE_CANDLE_SINCE_N3, NO_OPPOSITE_CANDLE_AT_BREAKOUT |

Record a choice with `Variant.choose(id, alternative, owner=...)`. Each combination is a separate
variant and a separate trial; alternatives are never pooled.

## Reuse map (FB-2533)
| Need | Reused | New here |
|---|---|---|
| Pivots | `patterns/pivots.py` reviewed; its confirmation timing differs, so `four_node/nodes.py` keeps `pivot_at` and `confirmed_at` explicitly | yes |
| SMMA | `kernel/indicators.py` has EMA/SMA, not Wilder SMMA | `four_node/indicators.py` |
| Minute data | `ingest/kite_minute.py` | — |
| Option routing | `signals/index_options.py` (underlying signal and option decision stay separate records) | — |
| Paper exits | `signals/exit_triggers.py`, `signals/exit_plans.py` | Trail rule in `rules.py` |

## Evaluation gates (FB-2534 to FB-2544)
No backtest has been run. Any test must first be preregistered in the trial ledger with universe,
parameter bounds, costs and gates; the 2026Q4 protected budget is spent, so a protected test needs
the owner's amendment or 2027Q1. Use `forecasting/index_15m.purged_splits` for walk-forward, adverse
same-bar ordering from `exit_triggers`, and the session-block bootstrap from `evaluation/scorecards`.
