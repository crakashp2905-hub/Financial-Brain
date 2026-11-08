# Algorithm library status (ALG-001..ALG-128)

Progress: [##############################] 128/128 (100.0%)

| Wave | Done | Total | Progress |
|---|---|---|---|
| W1 | 32 | 32 | [####################] 32/32 (100.0%) |
| W2 | 11 | 11 | [####################] 11/11 (100.0%) |
| W3 | 30 | 30 | [####################] 30/30 (100.0%) |
| W4 | 21 | 21 | [####################] 21/21 (100.0%) |
| W5 | 23 | 23 | [####################] 23/23 (100.0%) |
| W6 | 11 | 11 | [####################] 11/11 (100.0%) |

| ID | Wave | Status | Evidence | Note |
|---|---|---|---|---|
| ALG-001 | W1 | implemented | tests/test_algo_registry.py | Registry validator rejects missing provenance and duplicate IDs. |
| ALG-002 | W1 | implemented | tests/test_algo_registry.py | Indicators cannot be labelled executable; source types are fixed. |
| ALG-003 | W1 | implemented | tests/test_algo_registry.py | TradingView open-source label recorded as reference_only; licence needs a reference. |
| ALG-004 | W1 | implemented | tests/test_algo_registry.py | All MQL5 entries are reference_only unless a licence is recorded. |
| ALG-005 | W1 | implemented | tests/test_algo_registry.py | A test fails if Pine or MQL5 source markers appear in runtime modules. |
| ALG-006 | W1 | implemented | tests/test_algo_registry.py | docs/sources/CLEAN-ROOM.md states the rules; the test enforces the markers. |
| ALG-007 | W1 | implemented | tests/test_algo_registry.py | Vendor figures render only as SOURCE_CLAIM; edge labels are refused. |
| ALG-008 | W1 | implemented | tests/test_algo_registry.py | Registry hash changes when any source snapshot field changes. |
| ALG-009 | W1 | implemented | tests/test_algo_registry.py | Each entry carries a family; families are listed with their overlaps. |
| ALG-010 | W1 | implemented | tests/test_algo_registry.py | Unsuitable market tags are validated; every external item is paper-only. |
| ALG-011 | W1 | implemented | tests/test_algo_registry.py, tests/test_algo_families.py | Registry and strategies load with the network denied; no site is needed. |
| ALG-012 | W1 | implemented | tests/test_algo_specs.py | AlgorithmSpec validates fields, versions and sources. |
| ALG-013 | W1 | implemented | tests/test_algo_specs.py | BarClock: earliest fill is always the bar after the signal. |
| ALG-014 | W1 | implemented | tests/test_algo_specs.py | OrderSpec rejects incompatible kinds, prices and expiries. |
| ALG-015 | W1 | implemented | tests/test_algo_specs.py | Cash equity cannot be shorted in a spec. |
| ALG-016 | W1 | implemented | tests/test_algo_specs.py | CostModel with version; attach_cost_version refuses empty results. |
| ALG-017 | W1 | implemented | tests/test_algo_specs.py | ReferenceBehaviorFixture checks signal and decision indices. |
| ALG-018 | W1 | implemented | tests/test_algo_specs.py | Parity is fixed to BEHAVIOUR_ADAPTATION; exact parity is refused. |
| ALG-019 | W1 | implemented | tests/test_algo_specs.py | spec_sha256 is canonical and order-independent. |
| ALG-020 | W1 | implemented | tests/test_algo_specs.py, tests/test_algo_structure_exec.py | Higher-timeframe bars count only after they have closed. |
| ALG-021 | W1 | implemented | tests/test_algo_specs.py | Missing, zero or non-finite inputs return ABSTAIN. |
| ALG-022 | W1 | implemented | tests/test_algo_specs.py | Cash baseline returns zero; alpha vs cash is computed explicitly. |
| ALG-023 | W1 | verified_existing | tests/test_pattern_pivots.py | causal_pivots: a pivot is known only after its right-hand bars. |
| ALG-024 | W1 | implemented | tests/test_algo_structure_exec.py | significant_swing is ATR-scaled and dimensionless. |
| ALG-025 | W1 | implemented | tests/test_algo_structure_exec.py | HH/HL/LH/LL labels use confirmed pivots; prefix-invariant. |
| ALG-026 | W1 | implemented | tests/test_algo_structure_exec.py | Trend state from labels; higher timeframe must be closed. |
| ALG-027 | W1 | implemented | tests/test_algo_structure_exec.py | weekly_completed never reports a week before its Friday. |
| ALG-028 | W1 | implemented | tests/test_algo_final.py | Structure and entry clocks are separate; an entry advance never moves the structure clock. |
| ALG-029 | W1 | implemented | tests/test_algo_structure_exec.py | dedupe_events keys on lineage, timeframe and pivot identity; replays idempotent. |
| ALG-030 | W1 | verified_existing | tests/test_pattern_pivots.py | EventLog is append-only with timestamped history. |
| ALG-031 | W1 | implemented | tests/test_algo_structure_exec.py | PivotMark keeps the bar index and the recognised-at bar separately. |
| ALG-032 | W1 | implemented | tests/test_algo_structure_exec.py | structure_state labels flat, ranging, volatile and gapping sequences. |
| ALG-033 | W2 | implemented | tests/test_algo_trend_denominator.py | require_prior_trend keeps double tops only after a prior uptrend. |
| ALG-034 | W2 | implemented | tests/test_algo_trend_denominator.py | require_prior_trend keeps double bottoms only after a prior downtrend. |
| ALG-035 | W2 | verified_existing | tests/test_pattern_detectors.py | double_extrema is ATR-aware and duration bounded. |
| ALG-036 | W2 | verified_existing | tests/test_pattern_detectors.py | head_shoulders requires the right shoulder to be known before emitting. |
| ALG-037 | W2 | verified_existing | tests/test_pattern_detectors.py | head_shoulders_bottom mirrors the top rules. |
| ALG-038 | W2 | implemented | tests/test_algo_final.py | Lifecycle with LOCKED state; illegal moves refused; log replay detects tampering. |
| ALG-039 | W2 | implemented | tests/test_algo_structure_exec.py | Confirmation uses closes; a wick alone never confirms (test added). |
| ALG-040 | W2 | implemented | tests/test_algo_trend_denominator.py | formation_denominator reports formed, confirmed, failed and open counts; rejected lookalikes stay in formed. |
| ALG-041 | W2 | implemented | tests/test_algo_structure_exec.py | One event per pivot identity; restart-resistant through dedupe. |
| ALG-042 | W2 | verified_existing | tests/test_pattern_wave9_detectors.py | levels() never returns a profit target. |
| ALG-043 | W2 | verified_existing | tests/test_pattern_detectors.py, tests/test_pattern_wave9_detectors.py | Prefix invariance: later bars never change emitted events. |
| ALG-044 | W3 | implemented | tests/test_algo_breakouts.py | donchian_upper uses bars strictly before t. |
| ALG-045 | W3 | implemented | tests/test_algo_breakouts.py | basic_breakouts: long-only, reproducible reference level. |
| ALG-046 | W3 | implemented | tests/test_algo_breakouts.py | Turtle 20/10 constants are fixed and not tuned. |
| ALG-047 | W3 | implemented | tests/test_algo_breakouts.py | ATR-N sizing fails closed for bad stops or unfillable lots. |
| ALG-048 | W3 | implemented | tests/test_algo_breakouts.py | two_break_confirmation is a separate, labelled hypothesis. |
| ALG-049 | W3 | implemented | tests/test_algo_breakouts.py | trend_filtered_breakouts is a subset control, never a claimed improvement. |
| ALG-050 | W3 | implemented | tests/test_algo_breakouts.py | ATR threshold uses past bars only; exact threshold tested. |
| ALG-051 | W3 | implemented | tests/test_algo_final.py | Keltner EMA/ATR/ADX/volume breakout on a frozen grid of two trials; scale invariant. |
| ALG-052 | W3 | implemented | tests/test_algo_breakouts.py | gap_aware_fill fills at the open when a gap crosses the trigger. |
| ALG-053 | W3 | implemented | tests/test_algo_breakouts.py | incremental_pnl gives a paired difference against a baseline. |
| ALG-054 | W3 | implemented | tests/test_algo_session_mean.py, tests/test_algo_specs.py | NSE clock; foreign session keys are refused by the spec. |
| ALG-055 | W3 | implemented | tests/test_algo_session_mean.py | opening_range freezes only after the last minute of the window closes. |
| ALG-056 | W3 | implemented | tests/test_algo_session_mean.py | oco_intents and run_oco cancel the opposite side on entry. |
| ALG-057 | W3 | implemented | tests/test_algo_session_mean.py | run_oco flattens at the end-of-session time; no overnight carry. |
| ALG-058 | W3 | implemented | tests/test_algo_session_mean.py | range_excluded abstains on wide ranges and opening gaps. |
| ALG-059 | W3 | implemented | tests/test_algo_session_mean.py | retest_signal requires a retest strictly after confirmation. |
| ALG-060 | W3 | implemented | tests/test_algo_session_mean.py | failed_breakout_signal requires penetration then reentry. |
| ALG-061 | W3 | implemented | tests/test_algo_session_mean.py | placebo_signal_times gives the same clock with no geometry. |
| ALG-062 | W3 | implemented | tests/test_algo_session_mean.py | fill_if_available returns no fill for halts or zero volume. |
| ALG-063 | W3 | implemented | tests/test_algo_session_mean.py | ist_clock converts UTC to IST and refuses naive timestamps. |
| ALG-064 | W3 | implemented | tests/test_algo_breakouts.py | rolling_zscore uses only the bars before t. |
| ALG-065 | W3 | implemented | tests/test_algo_session_mean.py | session_vwap resets at each session. |
| ALG-066 | W3 | implemented | tests/test_algo_session_mean.py | vwap_rejection needs closed rejection bars. |
| ALG-067 | W3 | implemented | tests/test_algo_final.py | Research-only RSI/MACD/Keltner combined challenger, deterministic and bounded. |
| ALG-068 | W3 | implemented | tests/test_algo_session_mean.py | regime_abstain uses lagged returns only. |
| ALG-069 | W3 | implemented | tests/test_algo_session_mean.py | pair_spread_z fits the hedge ratio on training data only. |
| ALG-070 | W3 | implemented | tests/test_algo_session_mean.py | pair_is_tradable rejects cash equity short legs. |
| ALG-071 | W3 | implemented | tests/test_algo_session_mean.py | next_size_after_loss never increases size. |
| ALG-072 | W3 | implemented | tests/test_algo_session_mean.py | family_overlap_note keeps the K7 null visible. |
| ALG-073 | W3 | implemented | tests/test_algo_session_mean.py | after_cost_utility shows gross, net and turnover together. |
| ALG-074 | W4 | implemented | tests/test_algo_final.py | card_html shows source, platform, access date, SOURCE_CLAIM and paper-only status. |
| ALG-075 | W4 | implemented | tests/test_algo_registry.py | stale_check flags entries older than the allowed age. |
| ALG-076 | W4 | implemented | tests/test_algo_final.py | overlay_svg draws necklines with known_at and earliest entry; refuses unknowable or out-of-view overlays. |
| ALG-077 | W4 | implemented | tests/test_algo_final.py | state_style gives confirmed and unconfirmed states distinct styles; unknown states look faint. |
| ALG-078 | W4 | implemented | tests/test_algo_final.py | money() rounds to the tick and expresses quantity in whole lots, labelled INR. |
| ALG-079 | W4 | implemented | tests/test_algo_final.py | radar_filter filters by instrument, timeframe, regime, age and maturity; event ids are kept. |
| ALG-080 | W4 | implemented | tests/test_algo_final.py | compare_panel keeps gross, after-cost and incremental values as separate fields. |
| ALG-081 | W4 | implemented | tests/test_algo_families.py | Strategies package has no route to the order system; boundary test. |
| ALG-082 | W4 | implemented | tests/test_algo_final.py | why_not_log appends NO_TRADE records with a primary constraint; abstentions are never dropped. |
| ALG-083 | W4 | implemented | tests/test_algo_final.py | demo_page builds an offline page with the paper banner; no scripts or fetches; tested with network denied. |
| ALG-084 | W4 | implemented | tests/test_algo_structure_exec.py | realised_pnl raises NoFillRecord without a fill. |
| ALG-085 | W4 | implemented | tests/test_algo_structure_exec.py | next_open_fill_index: signal fills at the next bar, never earlier. |
| ALG-086 | W4 | implemented | tests/test_algo_structure_exec.py | resolve_intrabar takes the adverse branch when both levels hit. |
| ALG-087 | W4 | implemented | tests/test_algo_structure_exec.py | Gap through stop fills at the adverse open; tick rounding is adverse. |
| ALG-088 | W4 | implemented | tests/test_algo_structure_exec.py | participation_fill caps by volume and reports unfilled quantity. |
| ALG-089 | W4 | implemented | tests/test_algo_structure_exec.py | must_flat forces futures flat at expiry and intraday products at close. |
| ALG-090 | W4 | implemented | tests/test_algo_structure_exec.py | viewer_summary never sums open gross MTM with closed net. |
| ALG-091 | W4 | verified_existing | tests/test_pattern_pivots.py | paper.Ledger double-entry identity exists in the paper package. |
| ALG-092 | W4 | implemented | tests/test_algo_structure_exec.py | coverage_score reports the exact denominator. |
| ALG-093 | W4 | implemented | tests/test_algo_structure_exec.py | usable_for_paper is false without instrument, cost or spec identity. |
| ALG-094 | W4 | implemented | tests/test_algo_structure_exec.py | monte_carlo_drill is repeatable under a fixed seed. |
| ALG-095 | W5 | implemented | tests/test_algo_families.py | Every family declares null, alternative, sample and complexity. |
| ALG-096 | W5 | implemented | tests/test_algo_families.py | effective_trials de-duplicates by spec hash. |
| ALG-097 | W5 | implemented | tests/test_algo_final.py | matched_controls: same sector, never self, each control used once, on pre-event covariates. |
| ALG-098 | W5 | implemented | tests/test_algo_families.py | cohort_report keeps pre- and post-confirmation cohorts separate. |
| ALG-099 | W5 | verified_existing | tests/test_pattern_study.py | Purged split and embargo exist in patterns/study.py. |
| ALG-100 | W5 | implemented | tests/test_algo_families.py | paired_incremental uses net differences for ranking. |
| ALG-101 | W5 | verified_existing | tests/test_pattern_study.py | Cluster block bootstrap exists in patterns/study.py. |
| ALG-102 | W5 | implemented | tests/test_algo_final.py | PBO by combinatorial split; near chance for noise on average, near zero for a real edge. |
| ALG-103 | W5 | verified_existing | tests/test_pattern_study.py | Trial budget guard refuses protected real-data trials in CI. |
| ALG-104 | W5 | verified_existing | tests/test_pattern_learning_agent.py | Model registry versions are immutable; challengers cannot overwrite. |
| ALG-105 | W5 | verified_existing | tests/test_pattern_wave9_modules.py | scheduled_retrain uses matured labels only. |
| ALG-106 | W5 | implemented | tests/test_algo_families.py | verdict and release_gate never produce EDGE_CONFIRMED; assert_no_edge_label refuses it. |
| ALG-107 | W5 | implemented | tests/test_algo_families.py | StrategyAdapter protocol with a working adapter. |
| ALG-108 | W5 | implemented | tests/test_algo_families.py | Synthetic breakout, reversal and noisy-range generators. |
| ALG-109 | W5 | verified_existing | tests/test_pattern_detectors.py, tests/test_pattern_wave9_detectors.py | Prefix-invariance property tests across detectors. |
| ALG-110 | W5 | implemented | tests/test_algo_registry.py, tests/test_algo_specs.py | Registry and spec validators reject typos and inconsistent metadata. |
| ALG-111 | W5 | implemented | tests/test_algo_breakouts.py | Donchian signals are invariant to price scale and shift. |
| ALG-112 | W5 | verified_existing | tests/test_pattern_wave9_modules.py | run_resumable gives identical outputs after an interruption. |
| ALG-113 | W5 | implemented | tests/test_algo_final.py | CI step runs the status check; the strategy tests run under the existing CI pytest run. Linux leg pending push. |
| ALG-114 | W5 | verified_existing | tests/test_pattern_wave9_evidence.py | scripts/bench_patterns.py records before and after timings. |
| ALG-115 | W5 | not_applicable | - | Benchmark decision python_sufficient: projected 29 s against a 600 s budget. |
| ALG-116 | W5 | not_applicable | - | No native backend was introduced, so no parity tests are needed. |
| ALG-117 | W5 | implemented | - | This tracker produces the feature status report. |
| ALG-118 | W6 | verified_existing | tests/test_algo_families.py | python -m financial_brain.patterns.demo --offline runs with no credentials or network. |
| ALG-119 | W6 | implemented | tests/test_algo_final.py | Scripted walkthrough: chart, pattern, risk preview, paper intent, realised journal line. |
| ALG-120 | W6 | implemented | tests/test_algo_final.py | daily_paper_report lists trades, abstentions, unfilled trades, assumptions and the custody note. |
| ALG-121 | W6 | implemented | - | The tracker reports source claim, implemented, locally tested and forward-pending columns. |
| ALG-122 | W6 | implemented | tests/test_algo_families.py | card() links source, spec hash and paper-only banner. |
| ALG-123 | W6 | implemented | tests/test_algo_final.py | Corrupt checkpoint refused, missing source fails closed, empty results are answers not errors. |
| ALG-124 | W6 | implemented | - | The tracker writes docs/algorithm-library-status.md. |
| ALG-125 | W6 | implemented | tests/test_algo_families.py | A synthetic negative strategy is rejected by verdict(). |
| ALG-126 | W6 | implemented | tests/test_algo_final.py | Gate test: W1 to W4 all done and no strategy module reaches a broker, network or execution package. |
| ALG-127 | W6 | implemented | tests/test_algo_families.py | release_gate blocks promotion without forward observations. |
| ALG-128 | W6 | implemented | tests/test_algo_registry.py | Registry is the dated bibliography; rights are recorded per entry. |

Source claims are never edge claims. `EDGE_CONFIRMED` needs independent forward evidence (ALG-106, ALG-127).
