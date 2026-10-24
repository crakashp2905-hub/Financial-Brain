# Inverse strategy intelligence: implementation and limits

This is a research-only extension. It reconstructs observed decisions, not the
optimal trading policy, and it cannot promote a strategy or place an order.

## Repository compatibility audit

The existing architecture already provides the security master and point-in-time
data conventions, the India cost model, deterministic and probabilistic regime
components, strategy evaluation and replication, opportunity memory, paper trades,
an execution simulator, risk sizing, and trader-facing reports. The new code reuses
the execution simulator and leaves existing decision and ranking semantics intact.
In particular, `decisions/net_alpha.py` currently subtracts a risk penalty inside
its `net` property. The new `OpportunityEstimate` exposes **net alpha** before any
risk penalty, and a separate **portfolio utility**; existing callers are unchanged.

The `decisions` database table stores an action and timestamp but not the complete
market state at that time. `from_existing_decisions` therefore requires an external
as-of snapshot for each decision and refuses to synthesize missing state. The
`opportunity_memory` table records chosen/rejected candidates and realized forward
returns, but not all contemporaneous capital, execution, and portfolio constraints.
Its existing descriptive regret is useful but not unbiased policy evaluation.
The additive `inverse_decision_context` schema records as-of state, feasibility
constraints, and considered alternatives without changing existing tables. The
offline CSV/Parquet inference workflow does not require database access.
An additional `paper_rebalance_context` table holds immutable session-level paper
snapshots. Calling `paper.engine.run(..., remember=True, capture_context=True)` stores
the candidate ranking, cash, holdings, target, and feasibility settings once per
nonempty rebalance. It deliberately uses signal and execution **dates**, not an
invented intraday decision timestamp, and remains opt-in for write/cost control.
Within that snapshot, `known_before_execution` is separate from
`realized_after_execution`; the latter must never be used as a model input for
the former.
The read-only `fb inverse-audit` command reports coverage without treating a
rejected candidate as an observed order. On the local database inspected in this
pass it found 41 recorded decisions (26 BUY, 14 WATCH, one AVOID) and 175,430
opportunity-memory rows, of which 6,576 are explicit TAKEN/HELD/EXITED dispositions
and 168,854 are rejected candidates. These tables do not establish complete
point-in-time feature and portfolio-state snapshots, so the audit marks them
**not training-ready**. Counts are environment-specific, not a project benchmark.

Locally available price-derived histories and existing paper records can support
offline research. The repository does not by itself provide a complete, authorized
action history with timestamped feature snapshots, historical order books, historical
portfolio constraints, or an independent prospective sample. The optional REA
software-analysis repository is unrelated to trading alpha.

## Implemented pipeline

`research/inverse_strategy.py` validates authorized CSV/Parquet observations,
decision/state/execution timestamps, action vocabulary, provenance, and a strictly
allowlisted as-of feature vector. It offers nine deterministic known-rule synthetic
policies, chronological train/validation/holdout splits with an embargo, five CPU
classifiers (logistic, tree, forest, boosted, MLP), held-out action metrics, a
validation-measured decision-tree surrogate, and descriptive as-of regime outcome
groups. The action report includes a training-majority baseline for comparison.
Observed HOLD must be explicitly recorded. Rule extraction never uses the
holdout. The complete synthetic fixture cannot establish a market edge.

`research/trader_contracts.py` supplies an independent-validation gate for convex
ensembles, a calibration/abstention gate, selective-prediction coverage/error curves,
an itemized expected-net-alpha contract, portfolio-utility ranking, and feasible
descriptive counterfactual regret. `research/ensemble_walkforward.py` compares equal,
regularized static, regime-conditioned, and past-error-weighted online ensembles
using only outcomes observed before each forecast. These methods still need
time-stamped independently validated strategy forecasts and returns before real use.
`evaluate_ensemble_paper` applies an operator-fixed expected-alpha threshold and
compares after-cost return, alpha, drawdown, recorded turnover, and forecast error on
completed nonoverlapping paper decisions; it does not tune that threshold.
Its input must separate `cost_expected`/`benchmark_expected` (known at decision)
from `cost_realized`/`benchmark_realized` (known only afterward); only the former
can influence whether a trade is proposed.
The ensemble CSV also requires timezone-aware `decision_ts`, `estimate_asof`, and
`observed_at` fields; forecasts with `estimate_asof > decision_ts` are rejected.
The reliability gate needs prior calibration outcomes and abstains without
enough observations. It does not infer a success probability from model confidence.
`research/diagnostics.py` adds fixed-threshold selective paper-performance curves,
calibration bins with Wilson intervals, action timing/position errors, causal regime
transitions, a training-frozen feature-distance OOD flag, and a train-only kernel
two-sample drift gate. Neither drift flag is a failure-probability estimate.
`research/counterfactual_store.py` inserts immutable context snapshots and rejects
future-dated state; its alternative list explicitly records feasibility and reasons.

`research/execution_compare.py` compares six existing minute-bar execution algorithms.
The underlying simulator still forces end-of-window completion by default for legacy
callers, but research comparison now leaves residual orders unfilled by default and
reports fill and unfilled ratios explicitly.
Spread and impact are proxies, not reconstructed order-book quotes. `rea_evidence.py`
accepts only small JSON evidence exports with explicit enablement and authorization;
it hashes provenance, redacts obvious secrets, and requires manual review. It never
starts REA, analyzes an unknown binary, or changes production integrations. REA's
[official repository](https://github.com/morluto/rea) documents a local MCP/CLI
analysis tool; its actual output schema remains unverified here.

## Reproduction

From an installed editable package, or with `PYTHONPATH=src`:

```powershell
python -m financial_brain.research.inverse_strategy --synthetic momentum --seed 7 --rows 900 --rules-out rules.json
python -m financial_brain.research.inverse_strategy --history authorized.csv --authorized --rules-out rules.json
fb inverse --synthetic momentum --seed 7 --rows 900 --rules-out rules.json
fb inverse --history authorized.csv --authorized --report-out research_report.json
fb inverse-audit
fb inverse-tool compare research_report_a.json research_report_b.json
fb inverse-tool rules rules.json
fb inverse-tool regime authorized.csv --authorized
fb inverse-tool ensemble forecasts.csv --strategies s1 s2 --validated s1 s2 --paper --min-expected-alpha 0.002
fb inverse-tool selective completed_paper_decisions.csv
fb inverse-tool execution minute_bars.csv --qty 100 --fee-bp 5 --sigma-d 0.02 --half-spread-bp 2
fb inverse-tool counterfactual CONTEXT_ID
python -m pytest -q tests/test_inverse_strategy.py --basetemp D:\Finance-Brain\.pytest_inverse_temp
```

CSV/Parquet columns: `instrument`, `decision_ts`, `state_asof`, `action`,
`quantity`, `provenance`, and the ten named `FEATURES` in the module. Optional
`execution_ts`, `net_return`, and `regime_asof` enable additional checks/reports.
Use explicit timezone offsets. Never import broker credentials or a live order token.
The CLI prints action-prediction metrics and rule fidelity, never a trading return
for synthetic policies. Model selection uses validation macro-F1. A final holdout is
evaluated once per run; repeated manual redesign against it voids its independence.

The `fb inverse-tool` commands are read-only surfaces for saved reports, rules,
authorized histories, completed-paper reliability curves, validated-strategy
ensemble comparisons, minute-bar execution simulations, and stored counterfactual
context. The other research operations are also typed Python APIs: `compare_ensembles` requires
time-stamped, independently validated strategy forecasts; `ReliabilityGate.assess`
and `selective_performance` require earlier calibration/completed paper outcomes;
`rank_opportunities` requires explicitly calibrated reliability;
`compare_execution` accepts minute bars and reports residual fills; and
`record_rebalance_context` persists immutable paper context. None is invoked
implicitly by `fb inverse` or routes to live execution.

## Acceptance status and next empirical gates

- Implemented and tested: known-rule synthetic data, action and lagged-sequence baselines,
  causal input checks, measured-fidelity state and lagged-action surrogate rules,
  training-period EM-fitted Gaussian HMM with causal hidden-state filtering,
  descriptive regime performance/transitions, prequential ensemble comparison,
  calibration/abstention and selective paper-performance diagnostics, itemized alpha,
  execution-policy comparison with optional unfilled residuals, action timing/position
  error, train-frozen OOD and multivariate drift gates, feasible-regret filtering,
  additive context schemas and opt-in paper capture, read-only trader
  research commands, and optional REA evidence import.
- Incomplete: empirical ensemble validation on real forecasts;
  historical order-book-aware execution; complete automatic capture of intraday
  counterfactual constraints at every paper decision; full online integration of
  every research output into the main trading workstation; end-to-end existing-data result. These should not be
  represented as finished merely because interfaces exist.
- Blocked on data and prospective time: authentic timestamped decisions with complete
  as-of state; permission to use any external histories; historical liquidity and
  execution observations; enough independent, validated strategy forecasts; an
  untouched final market holdout and sustained paper-trading evidence. The preexisting
  registered trial history contains no confirmed deployable edge. No profitability
  claim follows from synthetic action reconstruction.

The next research milestone is to capture full as-of state and feasible constraints
alongside *every* paper decision, including explicit non-actions, then collect enough
prospective observations for walk-forward ensemble and reliability evaluation. The
existing 2022–26 final market holdout must not be inspected to select new variants.

## Verification in this implementation pass

The latest focused integration run passed 108 tests covering the new subsystem,
bounded search, the existing paper engine and opportunity memory, execution
attribution, and research paths.
`ruff check src tests` passed. The full repository pytest run was started but stopped
while still progressing; it is **not** a verified full-suite pass. Repository-wide
`ruff check .` also reports five pre-existing unused imports under `scripts/trials`;
these are outside the new source/test lint scope. No live order was placed.
