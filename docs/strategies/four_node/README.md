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

No permission-cleared XAU historical dataset is included in this checkout. The original XAU rules
remain a non-executable reference; no XAU backtest or performance claim is made.

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

On 2026-11-18 the owner asked for every combination to be tested instead of choosing one. All 128 failed after costs: see [results/README.md](results/README.md). `spec.india_variant(choices, owner=...)` builds any one of them.

Record a choice with `Variant.choose(id, alternative, owner=...)`. Each combination is a separate
variant and a separate trial; alternatives are never pooled.

## Reuse map (FB-2533)
| Need | Reused | New here |
|---|---|---|
| Pivots | `patterns/pivots.py` reviewed; its confirmation timing differs, so `four_node/nodes.py` keeps `pivot_at` and `confirmed_at` explicitly | yes |
| SMMA | `kernel/indicators.py` has EMA/SMA, not Wilder SMMA | `four_node/indicators.py` |
| Minute data | `ingest/kite_minute.py` | — |
| Option candidate routing | `paper/options_plan.py`, `paper/options.py` validate contract, observed quote, depth and cost hurdle; `forward_edge/option_surface.py` computes snapshot IV/Greeks | `four_node/options.py` binds an underlying thesis to a separate CE/PE candidate decision; no candidate ranking or runner wiring |
| Option replay/fills | `paper/options_runner.py` replays frozen intents against observed quotes and returns no-entry/no-exit outcomes without synthetic fills | Four-Node signal-to-intent integration, tick-driven stop/target/time exits and settlement remain open |
| Paper exits | `signals/exit_triggers.py`, `signals/exit_plans.py`; `paper/options_runner.py` supports planned-time quote replay | `Trail.on_quote()` in `rules.py`; no unified option position manager |

The earlier `signals/index_options.py` path did not exist and is not part of this architecture. Existing option modules are reusable components, not a complete Four-Node options system.

## Indicator and node data contracts (FB-2509 to FB-2513)
- Wilder SMMA is seeded with an n-value SMA; warm-up is `None`; a missing value resets its seed.
- The indicator API defaults to continuous-across-session smoothing. Callers can select
  `ON_SESSION_CHANGE` and pass aligned session IDs to reset each session. The 5-minute research
  study uses the continuous policy and does not infer a reset from bar spacing.
- Close SMMA periods are 8, 50, 100 and 200. The 20C cluster separately smooths Low, Close and
  High, with equal-length OHLC arrays and the same explicit reset policy.
- Daily Fibonacci snapshots carry version `four-node-daily-fib-v1`, a named anchor direction,
  source session ID and timezone-aware close/open timestamps. A source session closing at or
  after the evaluated session open is rejected. `events_for()` requires the dataset's ordered
  session dates and attaches both unmerged anchor hypotheses to each research event. The legacy
  numeric-level helper remains useful for fixtures but does not itself prove session-date provenance.
- Fractal ties are configurable as `STRICT`, `LEFTMOST` or `RIGHTMOST`; left/right windows must be
  positive. `build(..., min_swing=...)` enforces alternating highs/lows and the minimum price
  distance. Nodes retain pivot, confirmation and observation indices, stable IDs, parent ancestry
  and source bar IDs. Without supplied source IDs, deterministic local bar-index IDs are used;
  production ingestion should supply provider event IDs.
- `four_node.stream.FourNodeStateMachine` consumes immutable node-confirmation, completed-bar and
  session-reset events. Same event IDs with identical payloads replay idempotently; conflicting
  payload reuse is rejected. It resets on session rollover, supports explicit reset, expires old
  patterns, and refuses breakouts unless all three nodes were observable before that bar. A close
  breakout produces a paper/research candidate whose actionable bar is strictly later. Opposing
  same-bar candidates fail closed. The machine has no order or broker capability.
- Setup evaluation emits individual passed/failed predicates. D1/D2 equality policy is explicit
  per evaluation; barrier clearance checks only levels ahead in the trade direction. The original
  ambiguity variants remain separate and unresolved until independently recorded; the stream
  machine's pattern candidate is not itself an eligible trade.
- `Trail.on_quote()` uses the executable exit side (bid for a long, ask for a short); it rejects
  crossed/non-finite quotes and the trail ratchets without loosening. Tick-rounded initial stops
  round outward. Risk sizing validates the budget, prices and lot multiple and includes multiplier,
  spread and slippage. `InstrumentConfig` can retain session hours and a corporate-action factor,
  but production calendars/actions and validated per-symbol settings are not yet supplied.
- `eligibility.evaluate_instrument()` requires an effective-dated provider token, source hash,
  complete-session flag and turnover floor. Supported indices are signal references only;
  equities and ETFs must also be declared tradable. Unknown asset classes, missing provider
  identities, incomplete bars, stale validity and illiquid records fail closed. No actual
  provider snapshot is checked into the repository, so the function alone does not prove
  today's tradable universe.
- `four_node.replay.compare_stream_and_replay()` consumes original source timestamps and a separate
  availability clock, source-hashed OHLC and effective-dated instrument eligibility. It rebuilds
  pivots from the observed prefix only, waits for right-side confirmation bars, suppresses signals
  until the configured warm-up, resets on ineligible data and refuses missing/irregular bars. It
  runs the resulting immutable events online and in historical replay and exposes the parity check.
- The stream state machine retains integer bar indices for state transitions and also accepts
  source/availability timestamps for chronology checks and candidate lineage. Kite/provider event
  IDs, real receipt clocks, a production M1 adapter and durable runner/OMS integration remain open;
  this replay path is a validated research/paper data contract, not a live-feed claim.

## Evaluation gates (FB-2534 to FB-2544)
An exploratory 128-variant India grid has run, but it is not independent validation: all 128
variants expose the 2024+ TEST period and the source NPZ inputs are absent from this checkout. No
variant was profitable after costs in the recorded discovery or validation periods, and none is
promoted. Any new test must first be preregistered in the trial ledger with universe, parameter
bounds, costs and gates; the 2026Q4 protected budget is spent, so a protected test needs the owner's
amendment or 2027Q1. Use `forecasting/index_15m.purged_splits` for walk-forward, adverse same-bar
ordering from `exit_triggers`, and the session-block bootstrap from `evaluation/scorecards`.
