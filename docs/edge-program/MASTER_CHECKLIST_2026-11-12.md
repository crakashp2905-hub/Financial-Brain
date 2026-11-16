# Financial-Brain — Real Paper-Trading Profitability & Forward-Edge Master Checklist

**Edition:** 2026-11-12  
**Repository:** https://github.com/crakashp2905-hub/Financial-Brain  
**Audited main SHA:** `5b89e0b50c7177b5744a2a244d1cb0a7b1bc44a7`  
**Workstreams:** 16  
**Individually verifiable tasks:** 218  
**Primary deliverable:** A continuous, crash-safe, rights-compliant **paper trading platform that measures true executable forward net alpha** and remains able to abstain.  

> **Crucial distinction:** Software can reliably produce honest paper outcomes, not guaranteed positive returns. The research objective is to **discover and verify positive after-cost expected returns**. A positive report created by discarding losses, retuning after looking at forward performance, or assuming ideal fills is a research failure. The system must explicitly report `NO_TRADE`, `LOSS`, `INSUFFICIENT_EVIDENCE` and `EDGE_NOT_CONFIRMED` when true.

## 0. Evidence from current code review

- `main` includes PR #20 (`5b89e0b`): read-only Kite quote adapter and prior 12 Ruff findings fixed. PR branch CI succeeded on Ubuntu and Windows; at audit time the merge-commit run was still **in progress**. Re-check before release.
- `src/financial_brain/ingest/kite_live.py::forward_events` now creates normalized read-only Kite quote events, but at this audited SHA the `forward_events` payload carries **last_price**, source, and exchange; it does **not** carry executable bid/ask depth, spread, or available displayed liquidity.
- `src/financial_brain/forward_edge/cli.py::cmd_paper_run` and `forward_edge/pipeline.py::run_session` still operate on the **synthetic fixture pack**. The new pipeline has no completed live-market source-to-paper execution integration, constructs fresh in-memory journal/ledger objects, and uses an input close price for synthetic settlement.
- `src/financial_brain/evaluation/champion.py` still lacks `resolve_executable`; its `ranking_close_v1` should not serve as a formal promotion metric.
- `docs/forward-edge/TRACKER.md` reported **84 DONE / 56 PARTIAL / 12 BLOCKED** of 152 tasks. `docs/forward-edge/capability-matrix.json` reports **zero independently evidenced** tasks.
- `docs/forward-edge/strategy-eligibility.json`: **13 FAILED** protected trial entries, **12 SHADOW_ONLY** strategy-module entries. The 2026 Q4 protected research budget is **13/13 spent**. A new confirmatory trial requires an authorized governance amendment or a future permissible research window; never relabel a FAIL.
- `paper/intraday_runner.py` already contains a real Kite read-only session runner with persisted DuckDB paper trades, but its current live strategies are **ORB/GAP** according to `docs/PAPER-ENGINE-ALGORITHM-ADAPTERS.md`.
- `brain/09-Journal/2026-10-07 why the paper trades lose.md` reports ₹87,143 net losses over four sessions of historical intraday replay (237 trades), with near-zero gross edge and certain fees. The same note identifies more promising historical low-turnover momentum/volatility family observations, none established as a promoted edge.
- `brain/07-Research/Result of trading with the market trend.md` shows intraday trend-filter variants **still negative net out of sample** (approximately -21 to -25 bp/trade). Do not rescue this family by fitting a new filter to its failed forward observations.
- `brain/07-Research/Four edges that replicate, and the chart patterns that do work.md` reports within-tier predictive information for low volatility, momentum and 52-week-high measures; **these are correlated factor families, and evidence of predictiveness is not equivalent to executable profits**.
- The local F15 Windows log records **3,264 passed / 1 skipped**; fresh required-platform CI, integration and genuine observed forward sessions are separate release gates.

## 1. Engineering mission and non-negotiable rules

**North-star:** Develop one coherent pipeline: `authorized observed market events → causal versioned signal → pre-entry sealed decision → risk/cost gate → simulated eligible execution → canonical ledger → verified end-of-session NAV → matched controls → frozen forward test → independent edge verdict`.

**No external engineering dependency** means all strategy logic, simulators, book accounting, risk, tests, dashboards and reporting can be built in-house using the repository and local data. It does **not** mean an AI can invent market observations, a broker account, legally authorized data access, independent custody or live-session time. Those remain declared external inputs for genuine evidence; no AI intervention can bypass owner's regulatory/capital approval.

**Safety and rigor invariants:**

1. Never submit physical orders under `REPLAY`, `SHADOW`, `LOCAL_PAPER` or `LIVE_PAPER`; live cash requires separate human authorization and broker/venue compliance.
2. Never amend sealed historical trial data, reuse a protected test window, or improve an existing forward score by reclassifying bad days.
3. Use only features **available before the decision**; preserve data release delays, as-of membership, survivorship and corporate-action adjustments.
4. Never assume a last-traded price is an executable bid/ask. Filled quantity is not always desired quantity.
5. Measure NAV-level **after-cost** performance including cash, open losses, misses and no-trade days; no cherry-picked filled-trade win rate.
6. A positive historical mean or an unadjusted `t > 2` is not a confirmed after-cost trading edge. Account for costs, dependence, multiple search, and forward validity.
7. Explain what was *actually run* versus what was *only implemented*. The checkboxes below begin unchecked and need passing acceptance evidence.

## 2. Decision-gate definitions

| Gate | Minimum pass condition | If not met |
|---|---|---|
| `ENGINE_READY` | Required CI, reliable real-feed adapter, complete preflight, durable events, canonical ledger, fail-closed risk, fixture and market-data replay parity | STOP deployment of new paper engine |
| `PAPER_OBSERVABLE` | Actual eligible market sessions with quote coverage, pre-entry recorded decisions, simulated fills and daily reconciliations | Remain in fixture/replay mode |
| `CANDIDATE_ELIGIBLE` | Registered non-failed strategy, frozen spec/benchmark/selection universe, authorized trial and sufficient observability | SHADOW ONLY |
| `EDGE_CONFIRMED` | Registered *independent prospective* data, adequate effective sample, risk-matched positive net-alpha lower confidence bound, multiplicity control, robust costs, capacity and falsification tests | NOT CONFIRMED; no live cash |
| `BROKER_REHEARSED` | Real market-hours dry-run/reconciliation, broker authorizations and operational stress drills | No physical orders |
| `MICRO_LIVE_APPROVED` | Above gates, explicit owner permission, regulatory applicability check, capped economic loss, staged deployment and kill switch | Physical orders DISABLED |

### Formal economic definition

For each trading day `t`, compute portfolio return from the authoritative equity curve (including open positions and cash), and compare to a risk/market/turnover-appropriate, **predeclared** benchmark:

`r_strategy,t_net = (NAV_t - NAV_(t-1) - external_flow_t) / NAV_(t-1)`

`alpha_t_net = r_strategy,t_net - r_matched_benchmark,t_net`

Account for bid/ask crossing, commissions, transaction taxes, exchange fees, impact, margin, financing, roll, dividends, corporate actions and adverse execution according to instrument type. Use dependency-aware uncertainty quantification and multiplicity control. 

**Caution:** A proposed 60 eligible sessions is an *initial observation floor*, not a sufficient statistical guarantee. Compute the necessary sample with prospective variance, dependence, holding period and minimum economically important effect. For a 40-session momentum strategy, 60 adjacent daily NAV marks do not represent 60 independent returns.

## 3. Prioritized strategy-policy recommendation (not a trade recommendation)

| Lane | Initial policy | Why |
|---|---|---|
| Low-turnover equity momentum / 52-week-high / low-vol | Highest engineering priority; **development/shadow** until new authorized trial | Stronger historical factor signal at longer holding horizons; still no confirmed executable edge |
| Learned-model equity book | Shadow challenger; frozen lineage, execution cost and turnover | Historical positive observations require independent confirmation and multiplicity correction |
| Futures carry and basis | Specialized shadow; instrument rules, roll, funding and margin first | Small expected effects can be lost to basis and execution costs |
| Chart patterns, Donchian and new intraday ideas | Non-promotable hypothesis generation by default | New detection code does not establish after-cost edge |
| Prior ORB/GAP and related failed filters | No formal allocation unless separately authorized new protocol | Observed historical negative net results and cost drag |
| CASH / no-trade | Explicit control and allowed final allocation | Trading zero times can outperform a negative-expectation strategy |

## 4. Build sequence and milestone acceptance

| Wave | Focus | Core output | Release condition |
|---|---|---|---|
| W0 | Baseline / truth | Protected data, clean CI, governance, inventory | Can reproduce current environment without rewriting prior trials |
| W1 | Real source integration | Normalized authorized quote + depth archive, causal market events | Recorded observable event -> correct event-time replay |
| W2 | Canonical lifecycle | Strategy adapter -> risk -> OMS -> durable ledger | Crash-safe exact-once economic reconstruction on fixtures |
| W3 | Economic strategy books | Low-turnover candidate research and negative controls | Strategies are versioned; no hidden search or double-counted cash |
| W4 | Conservative fills/costs | Execution and full trading economics | Weak signals abstain; unfilled orders do not print P&L |
| W5 | Live paper sessions | Start/close/reconcile/custody/summary from actual source data | At least one fully captured exchange day with honest status |
| W6 | Frozen evidence | `resolve_executable`, true benchmark and statistical verdict | Reproducible, independently valid cohort reports; no forced positive result |
| W7 | Operator platform | Cockpit, alerts, risk, docs and recovery | Independent local operator reproduces a full session |
| W8 | Market-time testing | Accumulate eligible sessions under frozen protocol | Candidate meets/does not meet preregistered power and robustness criteria |
| W9 | Live eligibility | Broker rehearsal, compliance, owner-reviewed micro-capital proposal | Locked until *all* required gates pass |

## 5. Detailed engineering checklists

Each item has a stable `PE-####` ID, a concrete change, and an independently testable acceptance criterion. Tasks are **not** checked by default. When Codex finishes an item it must report a source diff, a named test, its result and a durable artifact path. No optimistic self-rating substitutes for running the test.

### Workstream A — Lock the truth, baseline, and release controls (14 tasks)

**Goal:** Create an unambiguous current-state audit; do not hide failing research or claim synthetic profits.

**Repository integration points:** `README.md`, `docs/trials/ledger.jsonl`, `docs/forward-edge/*`, `.github/workflows/ci.yml`, `scripts/`
- [ ] **PE-0001** — Record exact `main` SHA, PR graph, branch protections, runtime, and source capabilities; write `docs/edge-program/current-baseline.json`.  
  **Acceptance:** The baseline lists observed SHA, data freshness, access limitations and link to each evidence source.
- [ ] **PE-0002** — Prohibit edits to protected trial records and sealed historical outcomes.  
  **Acceptance:** Byte-for-byte or pre-existing canonical-hash comparison fails on any unauthorized change.
- [x] **PE-0003** — Snapshot and hash any actual local paper DuckDB, normalized tick archive, and strategy manifests before migration.
  **Acceptance:** Backup manifest restores original database and compares canonical checksums.
  **Completion evidence:** `docs/edge-program/pre-migration-backup-2026-11-15.json` records the manifest digest and restore result. A local-only backup covers 14 present artifacts (all top-level `paper*.duckdb`, portfolio/OMS databases, normalized quote archive, protected pins and three trial strategy manifests); every restored file is byte-identical to its recorded SHA-256, and every DuckDB opens read-only. Raw databases remain in ignored local `data/edge_program_backups/pre-migration-20261115/` and are not pushed.
- [x] **PE-0004** — Classify all 13 Q4 2026 registered failures as FAIL, retaining the original reasons.
  **Acceptance:** A registry diff detects any silent reclassification or deletion.
  **Completion evidence:** `tests/test_forward_edge_f00.py::test_all_13_registered_q4_trials_remain_fail_with_original_reason_codes` verifies all 13 Q4 trial rows remain `FAIL` with non-empty original failure-code lists. The full ledger SHA is pinned in `docs/forward-edge/protected-pins.json`; `test_retrospective_edit_of_the_trial_ledger_is_detected` verifies a reclassification changes the guard result.
- [ ] **PE-0005** — Expose CI lint failures and skip counts instead of reporting only the local pytest total.  
  **Acceptance:** Release gate blocks if lint, type checks, migrations, coverage or any OS required job fails.
- [ ] **PE-0006** — Require `main` review and status checks; verify settings via GitHub rather than a README assertion.  
  **Acceptance:** Branch protection API confirms required checks and reviews; until then a release stays blocked.
- [x] **PE-0007** — Inventory every market-data source, authorization, latency, access credential flag, and retention right without leaking secrets.
  **Acceptance:** Machine-readable source matrix labels MISSING, LICENSED, UNVERIFIED, and READY.
  **Completion evidence:** `docs/edge-program/market-data-sources.v1.json` records implemented Kite, exchange, index, filing, fund, news and macro sources plus missing licensed depth, expired-option history and US-market feeds. It records authorization/retention/latency evidence and credential-presence booleans only; `scripts/validate_market_data_sources.py` validates all four status labels, evidence paths, and credential-field shape in CI. Unknown rights and unmeasured latency remain explicitly `UNVERIFIED`; no source is promoted to `READY` without evidence.
- [x] **PE-0008** — Build a single capability matrix for real feed, replay, shadow, paper, broker dry-run and live-cash.
  **Acceptance:** Every displayed capability includes tested mode, fidelity, and evidence path.
  **Completion evidence:** `docs/audits/capability-matrix.v2.json` provides one row per critical software capability and explicitly covers REAL_FEED, RESEARCH_REPLAY, SHADOW, LIVE_PAPER, BROKER_DRYRUN, and LIVE_CASH. Every row records its tested mode, fidelity limits, test evidence, source paths, and remaining blockers. `scripts/validate_capability_matrix.py` rejects missing modes, evidence, or source/test paths; `scripts/source_inventory.py` reads the v2 matrix.
- [x] **PE-0009** — Write exact red/yellow/green release semantics for infrastructure, research and trading separately.
  **Acceptance:** A green test suite cannot turn research profitability or live trading green.
  **Completion evidence:** `docs/edge-program/release-semantics.v1.json` defines independent RED/YELLOW/GREEN conditions for infrastructure, research, paper trading, and live cash, plus unknown-evidence and overall aggregation rules. Research GREEN requires independent custody and positive after-cost confidence bounds; live-cash GREEN additionally requires research/paper GREEN, verified broker compliance, human approval, and rehearsal. Test success and infrastructure GREEN are explicit non-inputs to research or live-cash GREEN. `scripts/validate_release_semantics.py` enforces these separation rules in CI.
- [ ] **PE-0010** — Pin repository lockfile, Python version, seed, timezone and computational environment for reproducibility.  
  **Acceptance:** A clean offline install on Windows/Linux reproduces deterministic fixture hashes.
- [x] **PE-0011** — Capture source inventory and old/new forward-engine import dependency graph.
  **Acceptance:** A test rejects duplicate ownership of an authoritative order, ledger or position state.
  **Completion evidence:** `docs/edge-program/source-inventory.v2.json` snapshots all 594 Python files with per-file hashes, imports, curated test mappings and explicit unassessed ownership; `scripts/source_inventory.py --check` and CI enforce freshness. `docs/edge-program/forward-engine-dependency-graph.v1.json` records static import closures for the legacy intraday paper runner and `ForwardSessionRunner`, shared modules and source-tree hash. `tests/test_source_inventory.py::test_state_ownership_is_scoped_and_duplicate_authorities_are_rejected` verifies distinct books are scoped separately and rejects duplicate owners for the same scope/domain. `docs/edge-program/state-ownership.v1.json` explicitly leaves the Forward Edge canonical net ledger and durable position snapshot unestablished; this task does not claim those accounting gaps are solved.
- [ ] **PE-0012** — Create `edge-program` changelog with one row per deviation from the approved protocol.  
  **Acceptance:** Each deviation has author, clock, version, effect on evidence status, and rollback.
- [x] **PE-0013** — Define protected Q4 trial-budget check and Q1 2027 new-protocol pathway.
  **Acceptance:** No confirmatory trial is started without eligible budget or valid owner-approved ADR amendment.
  **Completion evidence:** `docs/forward-edge/trial-program.v1.json` pins Q4 2026 at 13 trials and leaves Q1 2027 `NOT_AUTHORIZED` with no budget or protocol ADR. `trials.assert_new_protected_trial_allowed()` refuses uncovered windows, duplicate trial names, exhausted budgets, missing/mismatched scoped human approvals, and Q1 proposals without both an owner-matched `Q1_PROTOCOL_AMENDMENT` and `PROTECTED_TRIAL`. Budget amendment approvals alone do not add capacity: the program must record a larger explicit budget and matching ADR. `tests/test_forward_edge_f00.py` covers the closed default, Q4 budget increase path, Q1 two-approval path, owner identity, hard budget cap, and agent refusal; `scripts/validate_trial_program.py` verifies current pins and exhausted Q4/Q1 status. No trial was run or authorized.
- [x] **PE-0014** — Block physical orders in all simulations and test a deliberately forged enabling flag.
  **Acceptance:** All fixture/replay/shadow/paper paths refuse physical broker order emission.
  **Completion evidence:** Tests deliberately set `PHYSICAL_ORDERS_ENABLED=True` and `FB_LIVE_ORDERS=1`, replace `KiteBroker.place` with a call recorder, and exercise fixture-backed `ForwardSessionRunner` paper execution plus the Kite-quote intraday paper and replay paths; no broker placement occurs and simulated fills report `physical_order_submitted=False`. The mode-gate test checks every `SessionMode` including `SHADOW` under the forged build flag. `tests/test_oms.py::test_the_order_path_lives_in_one_file_only` constrains Kite order transport to the separate OMS broker module. Focused forged-flag tests passed. This establishes simulation isolation; it does not disable the independent OMS live-order route.

### Workstream B — One integrated, authoritative forward session (15 tasks)

**Goal:** Turn separate forward-edge primitives into the same persistent market-session lifecycle used by actual paper trading.

**Repository integration points:** `forward_edge/pipeline.py`, `forward_edge/cli.py`, `paper/intraday_runner.py`, `forward_edge/durable.py`, `forward_edge/decision_custody.py`
- [x] **PE-0015** — Define versioned `ForwardSession` state machine: PLANNED, PREFLIGHT, OPEN, DRAINING, CLOSED, INCOMPLETE, REJECTED.  
  **Acceptance:** Invalid transitions fail closed and session IDs cannot be reused with different metadata.
- **Completion evidence:** `src/financial_brain/forward_edge/session_state.py`; `tests/test_forward_edge_session_state.py` (4 passed).
- [x] **PE-0016** — Define `ForwardRunConfig` containing exchange date, cohort, strategy SHA, benchmark, instrument universe, capital, constraints and ledger IDs.  
  **Acceptance:** Canonical config hash is invariant across process restarts and OSes.
- **Completion evidence:** canonical JSON/date/decimal/universe hashes persist with the session; `tests/test_forward_edge_session_state.py` (4 passed).
- [x] **PE-0017** — Implement `ForwardSessionRunner` that consumes normalized event streams, not fixture dictionaries.  
  **Acceptance:** One fixture adapter and one actual-market adapter call the exact same runner.
- **Completion evidence:** `session_runner.py::ingest` and `ingest_kite_archive`; `tests/test_forward_edge_session_runner.py::test_fixture_and_kite_adapters_enter_the_same_normalized_runner` (fixture and persisted Kite events use the same runner; archived non-session-date quotes are excluded).
- [ ] **PE-0018** — Refactor fixture-only `pipeline.run_session` to delegate to the canonical runner without changing golden outcomes.  
  **Acceptance:** All existing fixture tests pass and a trace diff shows equivalent behavior.
- [ ] **PE-0019** — Connect existing `paper/intraday_runner.py` as a read-only producer of observed quote events.  
  **Acceptance:** Actual paper-runner events are normalized with exchange, receive and availability clocks.
- [ ] **PE-0020** — Connect swing/position/momentum paper books through horizon-aware adapters, not intraday callbacks.  
  **Acceptance:** A 20-session signal survives overnight and never receives same-day forced expiry.
- [ ] **PE-0021** — Wire `forward_edge/durable.py` to the runner's event journal and decision manifests.  
  **Acceptance:** Crash at each lifecycle boundary produces exactly one economic event after recovery.
- [ ] **PE-0022** — Implement a transactional outbox for simulated execution and journal acknowledgements.  
  **Acceptance:** Failed side effect is UNKNOWN and cannot be marked FILLED without an observed simulation receipt.
- [x] **PE-0023** — Persist preflight, every data rejection, decision, risk verdict, order intent and fill.
  **Acceptance:** Daily record can be reconstructed from durable append-only events after deleting ephemeral views.
  **Completion evidence:** `runner_audit_log` is immutable by SQLite update/delete triggers and records preflight, normalized market events, ingest rejections, decisions, canonical source-bound decision manifests, explicit risk verdicts, paper intents, execution receipts and close reports. `ForwardSessionRunner.rebuild_materialized_views()` reconstructs query tables from this log. Paper intents require an allowed verdict and a manifest selection that covers the order quantity; each intent carries the manifest digest. `tests/test_forward_edge_session_runner.py::test_append_only_audit_rebuilds_daily_views_and_records_data_rejection` verifies view deletion/rebuild equivalence and durable rejection; `test_paper_intent_requires_canonical_manifest_and_respects_its_quantity` verifies manifest gating.
- [x] **PE-0024** — Use a durable per-strategy session key and attempt ID; do not overwrite `(day, mode)` results.  
  **Evidence:** `ForwardRunConfig` canonically persists mode and attempt ID; `session_attempt_key()` derives stable per-strategy/date/cohort/mode/attempt keys; `tests/test_forward_edge_session_state.py::test_strategy_attempt_keys_are_durable_and_never_overwrite_prior_attempt` verifies a second attempt appends without changing the first record.
  **Acceptance:** A second attempt appends a new immutable attempt; previous results are unchanged.
- [ ] **PE-0025** — Establish a single clock provider injecting exchange, receive, available, decision, and simulated execution times.  
  **Acceptance:** Tests with skewed or reordered clocks prevent any event from informing an earlier decision.
- [ ] **PE-0026** — Implement realistic session transitions for holidays, special sessions, feed-loss and premature termination.  
  **Acceptance:** Each planned trading date yields CLOSED, NO_TRADE or INCOMPLETE, never silently disappears.
- [ ] **PE-0027** — Implement `fb forward real-paper --date ... --cohort ... --confirm-paper-only`.  
  **Acceptance:** On local archived session data it completes with zero network access or broker orders.
- [ ] **PE-0028** — Implement `fb forward session --resume ...` after crash.  
  **Acceptance:** Restart produces identical risk/positions/cash and never repeats a fill.
- [ ] **PE-0029** — Implement start-of-day and end-of-day atomic checkpoints with event-hash and ledger-hash comparisons.  
  **Acceptance:** Reprocessing a day is a verification-only operation unless a new version is explicitly created.

### Workstream C — Observe real prices, not imagined fills (15 tasks)

**Goal:** A positive result is meaningful only when quotes, depth and latency justify executable costs.

**Repository integration points:** `ingest/kite_live.py`, `providers/kite_data.py`, `forward_edge/market_data.py`, `forward_edge/execution.py`, `paper/intraday_runner.py`
- [ ] **PE-0030** — Extend `forward_events` beyond `last_price` to best bid, ask, visible quantities, depth levels, volume, session high/low and exchange status when available.  
  **Acceptance:** Payload contract rejects absent mandatory fields for a fill-capable quote.
- [ ] **PE-0031** — Separate LTP-only observations from executable bid/ask observations.  
  **Acceptance:** LTP-only signals may be analyzed but cannot produce promotion-grade fill prices.
- [ ] **PE-0032** — Store immutable raw source payload hash and normalized event with exchange, receive, available, and ingest clocks.  
  **Acceptance:** Raw-to-normalized lineage is reconstructible for any fill.
- [ ] **PE-0033** — Create per-instrument versioned instrument rules: tick, lot, freeze, expiry, session and status.  
  **Acceptance:** Orders violating effective-date rules are refused without synthetic repair.
- [ ] **PE-0034** — Implement WebSocket or authorized read-only polling adapter with explicit provider provenance.  
  **Acceptance:** Mock feed and licensed feed share the same event contract; no credentials appear in logs.
- [ ] **PE-0035** — Capture reconnects, gaps, duplicates, last successful heartbeat and dropped packet count.  
  **Acceptance:** Fault injection triggers stale-feed NO_TRADE and leaves a count in session completeness.
- [ ] **PE-0036** — Archive bid/ask/depth alongside time-aligned trades and minute candles where source permits.  
  **Acceptance:** Each proposed execution receipt references the exact input quote IDs and fill-fidelity tier.
- [ ] **PE-0037** — Use source-specific timestamp tolerances; never silently replace missing exchange time with receipt time.  
  **Acceptance:** Test missing, naive, reversed, future and stale timestamps.
- [ ] **PE-0038** — Construct full exchange calendar for eligible dates, holidays, special sessions and instrument-specific trading windows.  
  **Acceptance:** Golden tests cover weekends, expiries and known special-session mechanics.
- [ ] **PE-0039** — Apply point-in-time corporate actions and symbol/ISIN succession for equity holdings.  
  **Acceptance:** Delisted and suspended securities remain in outcome denominators.
- [ ] **PE-0040** — Build quote-quality rules for crossed books, locked books, zero depth, circuit bands and auction periods.  
  **Acceptance:** Invalid quotes disable execution rather than interpolate a favorable spread.
- [ ] **PE-0041** — Preserve market-data source access rights, including what may be persisted or redistributed.  
  **Acceptance:** Right-not-known causes usage block rather than unlicensed capture.
- [ ] **PE-0042** — Compute opportunity-weighted coverage: dates, instruments, notional, candidate signals, filled/missed quotes.  
  **Acceptance:** A session report includes missing-data and lost-opportunity denominators.
- [ ] **PE-0043** — Compare archived live ticks and next-day authorized historical bars for divergence.  
  **Acceptance:** Disagreement is logged and the result is unverified until adjudicated.
- [ ] **PE-0044** — Build offline capture-replay equivalence harness from locally recorded events.  
  **Acceptance:** Identical normalized events yield identical signal and order-intent hashes.

### Workstream D — Inventory, qualify and freeze strategy candidates (15 tasks)

**Goal:** Allocate research attention to economic mechanisms rather than multiplying untested indicators.

**Repository integration points:** `forward_edge/eligibility.py`, `forward_edge/registry.py`, `strategies/`, `evaluation/`, `paper/`, `patterns/`, `docs/trials/`
- [ ] **PE-0045** — Enumerate every strategy *variant*, not merely the 12 Python strategy modules: paper zoo, factors, technical, chart-pattern, options, futures and existing ranking portfolios.  
  **Acceptance:** Registry inventory has unique `(family, name, version, asset, clock)` with source hash.
- [ ] **PE-0046** — Retain all previously failed trials as historical failures.  
  **Acceptance:** Registration rejects an existing FAIL trial under a renamed ID with identical parameter hash.
- [ ] **PE-0047** — Mark unregistered and unapproved strategies SHADOW_ONLY.  
  **Acceptance:** They generate observations but not formal forward paper-order performance claims.
- [ ] **PE-0048** — Define human-approved paper eligibility with immutable trial charter, first observation date and benchmark.  
  **Acceptance:** Paper status cannot be entered using an AI-only signature.
- [ ] **PE-0049** — Register 12-1 cross-sectional momentum and 52-week-high as an *exploratory family*, not as a continuation of failed M1.  
  **Acceptance:** Spec references past failed M1 and labels any new specification as distinct, not validated.
- [ ] **PE-0050** — Register low-volatility and ATR-ranked ideas as overlapping factor families, not independent discoveries.  
  **Acceptance:** Correlation-aware family grouping prevents duplicate hypothesis counting.
- [ ] **PE-0051** — Register low-turnover risk-aware momentum portfolios with explicit target turnover, no-trade band and capital limit.  
  **Acceptance:** Strategy outputs portfolio target weights and cash days at its scheduled rebalance only.
- [ ] **PE-0052** — Register learned-model score variants as challengers, with feature lineage and embargo constraints.  
  **Acceptance:** Training can read only declared development slices; every prediction includes model/data/config SHA.
- [ ] **PE-0053** — Register futures carry and basis candidates separately with roll/funding/settlement contracts.  
  **Acceptance:** They remain SHADOW until venue-specific economics and governance are complete.
- [ ] **PE-0054** — Keep unqualified intraday ORB/GAP, direction filters, and chart patterns in non-promotable monitoring.  
  **Acceptance:** Baseline negative results remain visible and unqualified signals cannot consume the formal trial budget.
- [ ] **PE-0055** — Create one bounded **research-only** mechanism library: trend, volatility, event conditioning, liquidity, and execution-policy alternatives.  
  **Acceptance:** All variants are logged before evaluation; no automatic live-selection of the maximum return.
- [ ] **PE-0056** — Require mechanism hypothesis: why edge exists, why it may persist, who pays, market regime and falsification condition.  
  **Acceptance:** No candidate can be registered with only indicator parameters and a backtest plot.
- [ ] **PE-0057** — Tag each feature with economic horizon, release delay, refresh cadence, and expected signal half-life.  
  **Acceptance:** A 20-session effect cannot be presented as a five-minute alpha without new evidence.
- [ ] **PE-0058** — Build a version-diff tool for parameters, filters, horizons, universe, cost model and code.  
  **Acceptance:** Any economic change after forward start forces a new trial ID and schedule.
- [ ] **PE-0059** — Freeze all benchmark definitions before examining forward outcomes.  
  **Acceptance:** Matched controls and risk-free/cash treatment are part of charter SHA.

### Workstream E — Build economically sensible candidate portfolios (14 tasks)

**Goal:** Use cost-aware selection, conservative sizing and cash abstention to avoid turning a weak predictor into a guaranteed fee leak.

**Repository integration points:** `evaluation/momentum_book.py`, `evaluation/families.py`, `evaluation/learned_model.py`, `portfolio/optimizer.py`, `forward_edge/portfolio_risk.py`
- [ ] **PE-0060** — Implement signal-to-target portfolio conversion with one shared capital allocator.  
  **Acceptance:** All strategies compare against the same starting NAV and use explicit CASH allocation.
- [ ] **PE-0061** — Add minimum *forecast net advantage* hurdle before any paper entry.  
  **Acceptance:** Low-edge candidate becomes NO_TRADE and its rejected forecast is retained for counterfactual analysis.
- [ ] **PE-0062** — Calibrate threshold only on development/training slices; freeze the resulting threshold before validation.  
  **Acceptance:** Tests reject dynamic threshold tweaks after a forward drawdown.
- [ ] **PE-0063** — Implement turnover-aware no-trade bands and rebalance trigger.  
  **Acceptance:** Stable portfolio with unchanged signals does not churn solely because of fee-induced equity drift.
- [ ] **PE-0064** — Implement rank/weight diversification across names and sectors with explicit max positions.  
  **Acceptance:** Synthetic concentrated inputs are limited or produce cash allocation.
- [ ] **PE-0065** — Use signal-strength/uncertainty scaling calibrated before the paper schedule.  
  **Acceptance:** Uncertain alpha reduces size, never increases it to reach a profit target.
- [ ] **PE-0066** — Implement factor-neutral or beta-aware comparisons as alternate registered candidates, not invisible preprocessing.  
  **Acceptance:** Each alternative carries separate strategy SHA and performance record.
- [ ] **PE-0067** — Build expected cost-to-forecast benefit ratio with safe abstention if forecast is not calibrated.  
  **Acceptance:** Placeholder `expected_net=0` does not authorize a trade.
- [ ] **PE-0068** — Compare 20/40/60-session economic horizons only in research-development data under a declared search budget.  
  **Acceptance:** Report every tried horizon and do not reopen the protected confirmatory data.
- [ ] **PE-0069** — Measure carry, dividends and residual cash yield with effective dates.  
  **Acceptance:** Portfolio total return reconciles with income entries and capital activity.
- [ ] **PE-0070** — Introduce inventory-aware sell rules to avoid unnecessary close-and-reopen of positions.  
  **Acceptance:** Round-trip fees only arise from actual changed inventory.
- [ ] **PE-0071** — Restrict participation and notional to observed liquidity with margin of safety.  
  **Acceptance:** Orders with insufficient historical/observed capacity are withheld.
- [ ] **PE-0072** — Add market-exposure ceiling and correlated-book aggregation.  
  **Acceptance:** Multiple strategy books cannot unknowingly exceed total account risk limits.
- [ ] **PE-0073** — Produce a Pareto trade-off of net performance, drawdown, turnover, capacity and complexity on training data only.  
  **Acceptance:** Chosen candidate and all rejected candidates are archived with explicit reason.

### Workstream F — Realistic simulation, transaction costs, and execution uncertainty (15 tasks)

**Goal:** Protect against the easiest way to manufacture paper gains: favorable assumed fills.

**Repository integration points:** `forward_edge/execution.py`, `execution/simulator.py`, `costs/india.py`, `paper/execution_audit.py`, `execution/oms/`
- [ ] **PE-0074** — Implement marketable-limit fill from post-decision bid/ask and eligible depth, not decision-bar close or LTP.  
  **Acceptance:** A one-tick worse quote changes fill economics; missing depth yields insufficient evidence.
- [ ] **PE-0075** — Enforce `order_eligible_time >= decision_available_time + modeled_latency`.  
  **Acceptance:** A quote published before the order was actionable cannot satisfy its fill.
- [ ] **PE-0076** — Simulate zero fills, partial fills, cancels, expiry, rejected requests and venue halts.  
  **Acceptance:** Every order ends in reconciled lifecycle state with correct inventory.
- [ ] **PE-0077** — Model spread, brokerage, statutory levies, exchange fees, GST, stamp duty and applicable taxes with versioned tables.  
  **Acceptance:** Round trip charge matches hand-calculated scenarios at effective dates.
- [ ] **PE-0078** — Separate entry and exit impact, exchange-fee effects and taxes from bid/ask crossing.  
  **Acceptance:** Costs are neither omitted nor double counted.
- [ ] **PE-0079** — Keep three explicit execution fidelities: bar inference, observed quote simulation, observed broker fill.  
  **Acceptance:** No aggregated performance silently mixes these fidelity tiers.
- [ ] **PE-0080** — Implement conservative bid/ask and adverse-selection assumption for stop and target collisions.  
  **Acceptance:** Ambiguous OHLC path is not resolved in the strategy's favor.
- [ ] **PE-0081** — Build slippage-calibration buckets by spread, ADV, time-of-day, participation and volatility.  
  **Acceptance:** Parameter changes require observed inputs or are flagged uncalibrated.
- [ ] **PE-0082** — Add scenario stress at 1.0x, 1.5x and 2.0x plausible costs plus worse fill rates.  
  **Acceptance:** A fragile candidate's report explicitly fails robust economics.
- [ ] **PE-0083** — Simulate missed fills and opportunity cost; do not fill trades retrospectively after target moves.  
  **Acceptance:** Missing fill outcomes are retained in the intention-to-trade denominator.
- [ ] **PE-0084** — Measure trading cost in both rupees and basis points per filled notional and NAV.  
  **Acceptance:** Cost-drag bridge reconciles to realized net book return.
- [ ] **PE-0085** — Measure model shortfall versus actual subsequent executable quotes at order arrival.  
  **Acceptance:** Shadow calibration never claims direct broker fill observations.
- [ ] **PE-0086** — Require accurate Indian-market tick, lot, freeze, margin and exchange-session rules by version.  
  **Acceptance:** Order validity refuses missing source-specific rules.
- [ ] **PE-0087** — Build execution model registry and immutable model IDs.  
  **Acceptance:** Every paper fill includes model, fee schedule, latency model and data-snapshot hashes.
- [ ] **PE-0088** — Conduct quote perturbation tests: worse spreads, gaps, late feeds, missing depth and double prints.  
  **Acceptance:** P&L cannot improve when buy costs increase or sell proceeds decrease, holding other things constant.

### Workstream G — Canonical OMS, cash, margin and economic accounting (14 tasks)

**Goal:** One order book and one financial truth are prerequisites for claiming a profitable account.

**Repository integration points:** `kernel/oms.py`, `kernel/ledger.py`, `forward_edge/books.py`, `forward_edge/durable.py`, `paper/engine.py`, `paper/intraday_runner.py`
- [ ] **PE-0089** — Design canonical order/event API for equities, long/short, futures and options with explicit asset-class plugins.  
  **Acceptance:** All engines emit the same immutable Intent/Order/Fill/Mark event contract.
- [ ] **PE-0090** — Use authoritative double-entry cash and position ledger across every paper strategy book.  
  **Acceptance:** No duplicate order-specific cash balances can conflict with canonical account state.
- [ ] **PE-0091** — Make orders reserve cash/margin once and release only the unspent balance.  
  **Acceptance:** Randomized partial-fill/cancel sequences preserve buying-power invariants.
- [ ] **PE-0092** — Post fees on actual filled notional at entry and exit.  
  **Acceptance:** Equity/NAV bridge matches cash and fee accounts to the cent.
- [ ] **PE-0093** — Implement stock splits, dividends, suspensions, delistings and corporate-action cash flows.  
  **Acceptance:** Positions and cost basis survive corporate events without synthetic gains.
- [ ] **PE-0094** — Implement futures daily variation margin, roll and expiry settlement.  
  **Acceptance:** Futures cash/position identity holds across settlement sessions.
- [ ] **PE-0095** — Implement option premium, spread margin, exercise/assignment and physical settlement where relevant.  
  **Acceptance:** Expiry scenario tests reconcile both legs, margin and cash obligations.
- [ ] **PE-0096** — Support multiple currency accounts with conversion fixings and explicit FX P&L.  
  **Acceptance:** Cross-currency double entry balances per currency plus translation account.
- [ ] **PE-0097** — Separate realized, unrealized, carry, cash yield, financing, tax and slippage attribution.  
  **Acceptance:** No component of NAV is unaccounted for and sums close within rounding policy.
- [ ] **PE-0098** — Reconcile end-of-day quantities, fill receipts and marks independently.  
  **Acceptance:** Any discrepancy halts new orders until repair and documented sign-off.
- [ ] **PE-0099** — Persist risk-latch state, outstanding orders and reservations through restart.  
  **Acceptance:** Crash/replay has identical account hash and cannot spend capital twice.
- [ ] **PE-0100** — Detect and reject duplicate provider fill IDs and economically inconsistent corrections.  
  **Acceptance:** No duplicate fill can increase positions or P&L.
- [ ] **PE-0101** — Ensure outcomes include the entire strategy NAV, not just cherry-picked closed winning trades.  
  **Acceptance:** Open losing positions and cash days enter the same performance equity curve.
- [ ] **PE-0102** — Implement independent daily ledger auditor that recomputes balances from journal only.  
  **Acceptance:** Corrupt stored summary is flagged even when its underlying journal is valid.

### Workstream H — Governed, forward-only evidence and immutable sessions (14 tasks)

**Goal:** Make the proof believable before any statistical claim is emitted.

**Repository integration points:** `forward_edge/decision_custody.py`, `forward_edge/custody.py`, `evaluation/prospective.py`, `evaluation/champion.py`, `docs/trials/`
- [ ] **PE-0103** — Create frozen future cohort schedule with exchange dates, strategy IDs, benchmark, capital and stops before first observation.  
  **Acceptance:** Changing any component after start invalidates or versions the cohort; historic observations unchanged.
- [ ] **PE-0104** — Record canonical decision manifest before earliest feasible execution time.  
  **Acceptance:** Clock checker rejects same-bar or retroactively created decisions.
- [ ] **PE-0105** — Persist every manifest and outcome in the durable journal, including NO_SIGNAL, NO_TRADE, REJECTED and UNKNOWN_ORDER.  
  **Acceptance:** All scheduled dates are accounted for and missing cannot vanish silently.
- [ ] **PE-0106** — Integrate external signed receipt API *optionally* for formal independent attestation.  
  **Acceptance:** Without a trusted independent receipt all rows remain LOCAL_ONLY and cannot count as independently verified.
- [ ] **PE-0107** — Support internal append-only hash-chain as default local development evidence, without calling it independent.  
  **Acceptance:** Tests detect local mutation and clearly label its weaker trust model.
- [ ] **PE-0108** — Wire `evaluation/prospective.verified_days` to the canonical verifier and schedules.  
  **Acceptance:** Only fully anchored pre-entry records enter independently verified numerator.
- [ ] **PE-0109** — Implement deterministic cohort snapshot export for a second-machine independent replay.  
  **Acceptance:** Replayed equity/order hashes match and custody mismatches are reported.
- [ ] **PE-0110** — Implement `evaluation/champion.resolve_executable` from actual forward orders and marks, not `ranking_close_v1`.  
  **Acceptance:** Resolves non-overlapping scheduled cohorts with missing fills and cash days.
- [ ] **PE-0111** — Record the full attempted universe, rejected symbols, missing price paths and corporate actions.  
  **Acceptance:** Survivorship and missing-return sensitivity appear beside the main estimate.
- [ ] **PE-0112** — Define full strategy/evaluation identity with market calendar, data rights, model code and execution-cost version.  
  **Acceptance:** Unhashed changes force a new trial version.
- [ ] **PE-0113** — Prevent retrospective edits of cohort endpoints and benchmark after seeing P&L.  
  **Acceptance:** A test writes a favorable new benchmark after session close and verifies rejection.
- [ ] **PE-0114** — Separate exploratory shadow reports, historical replay, local paper and independently attested results at every API boundary.  
  **Acceptance:** UI and exports cannot mislabel replay as forward evidence.
- [ ] **PE-0115** — Use an eligibility gate to enforce actual trial budget: Q4 2026 exhausted unless authorized governance amendment.  
  **Acceptance:** Prospective promotion cohort cannot be created with an unapproved trial identity.
- [ ] **PE-0116** — Maintain independently reviewed exclusion log for genuine data corruption, never discretionary losing-session deletion.  
  **Acceptance:** Excluded dates remain in the denominator and are exposed in sensitivity reports.

### Workstream I — Statistical edge evaluation that survives scrutiny (16 tasks)

**Goal:** Translate daily trade outcomes into an uncertainty-aware economic claim, without excessive peeking.

**Repository integration points:** `forward_edge/evidence_stats.py`, `forward_edge/refinements.py`, `evaluation/champion.py`, `research/preregistry.py`
- [ ] **PE-0117** — Compute daily strategy total returns from actual NAV and external cash flows.  
  **Acceptance:** Cash days, open positions, funding and late fills affect every trading-day return.
- [ ] **PE-0118** — Compute matched benchmark net returns on the same dates and risk budget.  
  **Acceptance:** Excess return is not a raw rupee-profit comparison to an unmatched index.
- [ ] **PE-0119** — Report mean net alpha, annualized return, volatility, Sharpe, Sortino, drawdown, turnover and profit factor where appropriate.  
  **Acceptance:** Metrics declare sample duration, annualization assumptions and exact denominator.
- [ ] **PE-0120** — Use Newey-West or block bootstrap for overlapping holdings and serial dependence.  
  **Acceptance:** Effective sample size and chosen lags/block policy are frozen before outcomes.
- [ ] **PE-0121** — Use calendar and instrument clustering when analyzing multiple same-day trades.  
  **Acceptance:** Thousands of correlated trades cannot be misreported as independent days.
- [ ] **PE-0122** — Apply family-wise or false-discovery control across all registered strategy/horizon/filter searches.  
  **Acceptance:** P-value adjustment includes failures, not only the final favorite.
- [ ] **PE-0123** — Use White Reality Check or defensible SPA-style bootstrap for the full development search universe.  
  **Acceptance:** Report null construction, candidate count, resampling seed and sensitivity.
- [ ] **PE-0124** — Implement a truly prospective, non-overlapping champion/challenger evaluation cohort.  
  **Acceptance:** A champion cannot be selected based on the same returns used for final confirmation.
- [ ] **PE-0125** — Compute lower confidence bounds on daily mean net alpha and after-cost portfolio utility.  
  **Acceptance:** A positive point estimate with lower bound <=0 is not labeled CONFIRMED.
- [ ] **PE-0126** — Implement registered fixed-look checkpoints and early stopping for risk harm.  
  **Acceptance:** Unscheduled interim dashboards cannot promote a strategy based on a lucky week.
- [ ] **PE-0127** — Choose required sample size from detectable benefit, volatility, dependence, power and design parameters.  
  **Acceptance:** Hard-coded 60 sessions is treated as a floor, not guaranteed statistical sufficiency.
- [ ] **PE-0128** — Stratify outcome by volatility, bull/bear/sideways periods, liquidity, market-cap band and transaction-cost bucket.  
  **Acceptance:** Report fragmentation uncertainty instead of claiming every regime is profitable.
- [ ] **PE-0129** — Implement falsification tests for look-ahead, survivorship, delisting, corporate actions, stale quote, missing fills and factor overlap.  
  **Acceptance:** A failed critical falsification check automatically blocks promotion.
- [ ] **PE-0130** — Report alternative fill scenarios and best/worst-case bounds for unobservable queue position.  
  **Acceptance:** Alpha claim does not depend on optimistic passive fills alone.
- [ ] **PE-0131** — Show return distribution and top-contributor dependence including best month/day/stock.  
  **Acceptance:** Removing a few lucky events is visible without retroactively rewriting the protocol.
- [ ] **PE-0132** — Define gate `EDGE_CONFIRMED` only if registered, independent forward, adequate sample, positive robust lower bound, feasible fills, costs/stress, risk and integrity pass.  
  **Acceptance:** All-zero/favorable synthetic inputs cannot force an unjustified edge label.

### Workstream J — Loss attribution and evidence-driven research iteration (12 tasks)

**Goal:** Turn losing trades into valid hypotheses rather than post-hoc performance rescue.

**Repository integration points:** `paper/loss_review.py`, `paper/daily_results.py`, `forward_edge/research_tools.py`, `evaluation/half_life.py`
- [ ] **PE-0133** — Build a P&L bridge: forecast gross -> selection effect -> spread -> impact -> charges -> timing -> risk sizing -> realized net.  
  **Acceptance:** Every losing day sums to total portfolio loss without unexplained residual.
- [ ] **PE-0134** — Classify each losing trade: false signal, unfavorable regime, timing, execution cost, size, stale data, invalid universe or infrastructure.  
  **Acceptance:** Classifier stores evidence IDs and unknown category when proof is absent.
- [ ] **PE-0135** — Separate gross-negative trades from gross-positive-cost-erased trades.  
  **Acceptance:** Report counts, rupees and notional-weighted rates for both categories.
- [ ] **PE-0136** — Compare a trade to contemporaneous, size/liquidity/risk-matched no-trade/control portfolios.  
  **Acceptance:** Reports causal limitations and never infer 'filter helps' merely from dropped losers.
- [ ] **PE-0137** — Create bounded predeclared training-only ablations for entry, exit, holding horizon, portfolio width and turnover.  
  **Acceptance:** All combinations and negative results are logged; protected final window untouched.
- [ ] **PE-0138** — Compute time-to-peak information, signal half-life and turnover-to-alpha ratio for each family.  
  **Acceptance:** Decision horizon follows measured signal horizon only in new registered versions.
- [ ] **PE-0139** — Assess stop/target sensitivity on development data with same-bar collision penalties.  
  **Acceptance:** No favorable bar-path ordering drives improved results.
- [ ] **PE-0140** — Check whether trades occurred after market-data gaps, abnormal spread or high volatility.  
  **Acceptance:** System issues are separated from statistical strategy failure.
- [ ] **PE-0141** — Use a shadow counterfactual book for rejected opportunities with clearly modeled hypothetical fills.  
  **Acceptance:** Shadow results cannot be called observed cash P&L or promoted without registered controls.
- [ ] **PE-0142** — Produce causal candidate hypotheses from observed patterns in development data, with a prewritten falsifier.  
  **Acceptance:** LLM suggestions do not mutate running paper strategies or trial budgets.
- [ ] **PE-0143** — Require independent internal adversarial review of each proposed improvement before registering it.  
  **Acceptance:** Reviewer checks leakage, costs, multiple testing and chance fitting; reject/approve recorded.
- [ ] **PE-0144** — Implement degradation alarms for rolling net alpha, slippage, fill coverage and drift, using thresholds frozen before paper start.  
  **Acceptance:** Alarm suspends or retires candidate; it never silently edits profitable-looking parameters.

### Workstream K — Live paper operating discipline (14 tasks)

**Goal:** A session actually observed over time is the unit of proof, not a rerun of yesterday with new logic.

**Repository integration points:** `paper/intraday_runner.py`, `forward_edge/session_ops.py`, `forward_edge/cli.py`, `forward_edge/cockpit.py`, `scripts/paper_intraday.cmd`
- [ ] **PE-0145** — Add premarket preflight for market calendar, data entitlement, broker read-only session, disk, archive, costs, strategy version, risk and clocks.  
  **Acceptance:** Any missing mandatory input yields NO_TRADE and an operator-visible reason.
- [ ] **PE-0146** — Prepare frozen paper books for each eligible strategy with separate account IDs and capital.  
  **Acceptance:** One strategy cannot borrow another's cash without a registered allocation change.
- [ ] **PE-0147** — Start source capture before the earliest strategy decision clock.  
  **Acceptance:** Session start is logged with clock skew, quote-health and source entitlements.
- [ ] **PE-0148** — Allow read-only market data and never call broker.place_order from paper mode.  
  **Acceptance:** Integration test blocks all order endpoints and verifies no network order path.
- [ ] **PE-0149** — Write signals and abstentions before outcomes can be observed.  
  **Acceptance:** Every expected signal has sealed timestamp and input-hash proof.
- [ ] **PE-0150** — Apply pre-trade liquidity, spread, cost, exposure and model-confidence gates.  
  **Acceptance:** Each refused intent produces a durable, attributable skip record.
- [ ] **PE-0151** — Track working paper orders, cancels and missed fills throughout market session.  
  **Acceptance:** UI reports exact fills and remaining quantities rather than assuming complete trades.
- [ ] **PE-0152** — Support overnight holdings and exit orders at properly scheduled later sessions.  
  **Acceptance:** Holding-period clock does not reset at midnight or collapse to 15:15 exit.
- [ ] **PE-0153** — Automate end-of-session mark-to-market, order reconciliation and snapshot archive.  
  **Acceptance:** Next day opens on an exactly reconciled prior NAV and inventory.
- [ ] **PE-0154** — If feed ends early or clock fails, fail closed for new entries and preserve existing risk state.  
  **Acceptance:** Outage does not become a zero-loss successful session.
- [ ] **PE-0155** — Generate one signed/hashable daily bundle plus human-readable operator report.  
  **Acceptance:** A different workstation can independently reproduce that day's report from exported inputs.
- [ ] **PE-0156** — Monitor planned-versus-completed sessions with missing/unqualified and NO_TRADE denominators.  
  **Acceptance:** Weekend/holiday logic and incomplete days remain auditable.
- [ ] **PE-0157** — Measure opportunity counts, fee drag, gross/net returns, P&L contribution and quote coverage by book.  
  **Acceptance:** Reports never show only closed winners or only filled intents.
- [ ] **PE-0158** — Create a paper session runbook with premarket, intraday, close and exception handling in local timezone.  
  **Acceptance:** Offline walkthrough demonstrates all tasks without prior unstated knowledge.

### Workstream L — Conservative intraday and derivatives experiment lanes (12 tasks)

**Goal:** Keep risky short-horizon avenues alive as research but do not use them to conceal low expected edge.

**Repository integration points:** `paper/intraday.py`, `paper/options*.py`, `forward_edge/futures.py`, `strategies/breakouts.py`
- [ ] **PE-0159** — Disable automatic allocation of evidence-failed ORB/GAP to the formal paper champion book.  
  **Acceptance:** Existing failed cohorts remain queryable and new monitoring is SHADOW only.
- [ ] **PE-0160** — Build a timestamp-safe ORB inverse/fade hypothesis *only* in exploratory research; do not market it as superior.  
  **Acceptance:** It is stored with its own mechanism and cost budget, never relabeled as registered evidence.
- [ ] **PE-0161** — Test intraday low-turnover or selective-entry concepts in training-only replay with independent same-time controls.  
  **Acceptance:** Selectivity cannot be optimized on the eventual forward session schedule.
- [ ] **PE-0162** — Require intraday order book and spread coverage prior to testing sub-30-minute fill economics.  
  **Acceptance:** LTP-only intervals cannot contribute executable alpha.
- [ ] **PE-0163** — Use change-point/regime features only if they are observable before signal time.  
  **Acceptance:** Future prices and full-day regimes cannot influence morning entries.
- [ ] **PE-0164** — Model futures roll schedule, open interest, contract multiplier, variation margin and funding.  
  **Acceptance:** Net P&L correctly includes roll, expiry and overnight carry.
- [ ] **PE-0165** — For options, require timestamped bid and ask at each leg and venue-compatible lot and expiry.  
  **Acceptance:** Missing option quote for any leg makes the simulated spread unexecuted.
- [ ] **PE-0166** — Model multi-leg partial fill/legging exposure, spread margin and closeout risk.  
  **Acceptance:** Tail-risk and non-fill cases remain in account performance.
- [ ] **PE-0167** — Implement expiry/assignment/physical settlement and applicable India contract rules from effective-date tables.  
  **Acceptance:** Expiry stress tests show no unaccounted delivery or margin deficit.
- [ ] **PE-0168** — Build FX paper simulation only on explicitly legal/authorized instrument and venue specifications.  
  **Acceptance:** Unknown venue or jurisdiction becomes NO_TRADE.
- [ ] **PE-0169** — Track per-asset-class economic edge separately before an aggregate portfolio claim.  
  **Acceptance:** Equity success cannot mask losses in options/futures through undisclosed netting.
- [ ] **PE-0170** — Require explicit per-family evidence gates before capital increases or auto execution.  
  **Acceptance:** A newly developed strategy cannot bypass frozen-protocol and owner-review requirements.

### Workstream M — Learning and adaptive policy without contamination (12 tasks)

**Goal:** Improve forecasting and decision quality while keeping independent evaluation untouched.

**Repository integration points:** `evaluation/learned_model.py`, `forward_edge/governance.py`, `research/counterfactual_store.py`, `forward_edge/registry.py`
- [ ] **PE-0171** — Build reproducible offline training jobs from timestamped, versioned, matured outcomes.  
  **Acceptance:** Training manifest hashes code, feature schema, dataset and seed.
- [ ] **PE-0172** — Use purged/embargoed splits for overlapping holding horizons and nearby events.  
  **Acceptance:** No feature, target, label or revision from after decision time enters fitting.
- [ ] **PE-0173** — Separate supervised alpha forecast from execution-fill probability and risk forecast.  
  **Acceptance:** Components each have independently calibrated targets and quality metrics.
- [ ] **PE-0174** — Implement calibration for expected net returns and their uncertainty, not just direction accuracy.  
  **Acceptance:** Calibration curves and trading-impact plot are produced on untouched internal validation.
- [ ] **PE-0175** — Require feature availability timestamps in inference and batch-training schemas.  
  **Acceptance:** Future-availability perturbation tests show no leakage.
- [ ] **PE-0176** — Train only on properly matured, complete outcomes; quarantine open trades and pending settlements.  
  **Acceptance:** Model cannot ingest label generated from a future or unknown exit.
- [ ] **PE-0177** — Create signed/hashable challenger registry and promote only through future authorized evidence gates.  
  **Acceptance:** Automatic model training cannot promote itself because of training P&L alone.
- [ ] **PE-0178** — Use factor exposure and expected turnover as explicit regularizers.  
  **Acceptance:** Candidate comparison shows if apparent alpha is just size, momentum, beta or excess trading.
- [ ] **PE-0179** — Implement a counterfactual decision memory of rejected signals with explicit hypothetical nature.  
  **Acceptance:** Rejected opportunities are retained without inventing actual fills.
- [ ] **PE-0180** — Limit strategy/hyperparameter search budgets and require candidate-count correction.  
  **Acceptance:** Hidden optimization attempts count toward the testing family.
- [ ] **PE-0181** — Block online model weight updates during locked forward cohorts.  
  **Acceptance:** A live day with drift may trigger halt; it cannot rewrite running strategy identity.
- [ ] **PE-0182** — Support rollback to last approved version after forecast drift or harm alarms.  
  **Acceptance:** Version and capital changes are immutable events and require correct authorization.

### Workstream N — Trader-facing cockpit, reports, and confidence displays (12 tasks)

**Goal:** Make economic reality obvious: alpha, cost drag, uncertainty, missed fills and no-trade states.

**Repository integration points:** `forward_edge/cockpit.py`, `paper/workstation.py`, `paper/trader_view.py`, `forward_edge/cli.py`
- [ ] **PE-0183** — Create session overview for feed health, calendar, active version, broker read-only state and risk latches.  
  **Acceptance:** Stale or incomplete sources are shown prominently and block trade-readiness claims.
- [ ] **PE-0184** — Build strategy comparison table with gross, costs, net, benchmark net, alpha, confidence interval, drawdown, turnover, fidelity and sample.  
  **Acceptance:** Sorting cannot hide failed/insufficient outcomes by default.
- [ ] **PE-0185** — Show cumulative portfolio NAV including cash and open positions, not only realized trades.  
  **Acceptance:** Dashboard NAV reproduces canonical ledger row for every session.
- [ ] **PE-0186** — Show exact rejected/abstained/missed/no-signal counts with reason codes.  
  **Acceptance:** All planned dates appear in coverage denominator.
- [ ] **PE-0187** — Display simulated versus observed execution labels and inputs alongside each fill.  
  **Acceptance:** Users cannot confuse modelled quote fills with actual exchange fills.
- [ ] **PE-0188** — Provide per-trade drilldown: source quote -> signal -> risk -> intent -> fill -> ledger -> exit -> attribution.  
  **Acceptance:** Every field links to immutable journal IDs.
- [ ] **PE-0189** — Display provisional research ranking separately from verified executable forward champions.  
  **Acceptance:** A ranking diagnostic never receives a green tradable-edge badge.
- [ ] **PE-0190** — Display confidence intervals, multiplicity count, matched benchmark and estimated capacity beside any profitability claim.  
  **Acceptance:** No strategy is labeled 'proven' solely on win rate or positive total rupees.
- [ ] **PE-0191** — Expose paper-only risk kill switch and incident review, with human-controlled reset.  
  **Acceptance:** AI and untrusted UI actions cannot silently override a halt.
- [ ] **PE-0192** — Produce daily JSON/CSV/HTML reports from the same single versioned report schema.  
  **Acceptance:** Output totals match on different formats and OSes.
- [ ] **PE-0193** — Offer explicit 'insufficient evidence' status when inputs/observations are missing.  
  **Acceptance:** Fake all-green fixture tests never turn a real forward dashboard green.
- [ ] **PE-0194** — Ship a complete offline demo containing a win, loss, NO_TRADE, feed outage and UNKNOWN_ORDER.  
  **Acceptance:** New operator reproduces outcomes from one command and sees honest labels.

### Workstream O — Operational reliability, security, and release gate (12 tasks)

**Goal:** No trading edge survives if the engine invents cash, loses orders or silently skips losing days.

**Repository integration points:** `.github/workflows/ci.yml`, `forward_edge/durable.py`, `forward_edge/audit.py`, `tests/`
- [ ] **PE-0195** — Restore and maintain green `ruff`, mypy, full pytest, migrations, audit and dependency checks on each required OS.  
  **Acceptance:** Main commit has both Windows and Linux completed success runs.
- [ ] **PE-0196** — Add CI test enforcing suite's critical coverage floors for OMS, custody, market data, accounting, evaluation and risk.  
  **Acceptance:** Regressing a threshold fails CI before merge.
- [ ] **PE-0197** — Add mutation/property tests for P&L invariants, direction sign, fee impact and identifier idempotency.  
  **Acceptance:** Injected wrong-sign/duplicate-fill mutations are caught.
- [ ] **PE-0198** — Run crash-injection matrix at every risk decision, manifest write, order send, fill, cost posting and close.  
  **Acceptance:** Recovered cash/positions/receipt hashes equal uninterrupted simulation.
- [ ] **PE-0199** — Test network gaps, clock skew, holidays, corporate actions, symbol reuse, replay duplicates and stale ticks.  
  **Acceptance:** Every fault ends with explicit reason and preserved ledger integrity.
- [ ] **PE-0200** — Enable append-only archive rotation with manifest hash chain and tamper detection.  
  **Acceptance:** Corrupted middle records are refused rather than being treated as torn tails.
- [ ] **PE-0201** — Run offline backup-and-restore drill for all journal, quotes, ledgers, manifests and configs as one dependency set.  
  **Acceptance:** Restored engine produces identical end-of-day hashes with no original files mounted.
- [ ] **PE-0202** — Benchmark event throughput, process memory, report latency and disk saturation on a representative local dataset.  
  **Acceptance:** Limits and alerts are backed by recorded measurements.
- [ ] **PE-0203** — Implement least-privilege read-only Kite access for paper and prevent credential/token emission in logs.  
  **Acceptance:** Secret scanning and network-mocking tests show no accidental order call.
- [ ] **PE-0204** — Add dependency and license report with local artifact cache to preserve in-house reproducibility.  
  **Acceptance:** Build succeeds without downloading new proprietary services once dependencies are present.
- [ ] **PE-0205** — Require independent internal signoff for any source change affecting returns or account balances.  
  **Acceptance:** PR checklist identifies reviewers, scenario failures and reversibility.
- [ ] **PE-0206** — Audit every promoted capability against executable end-to-end evidence, not test-file existence.  
  **Acceptance:** Capability status cannot be upgraded without referenced passing test and artifact hash.

### Workstream P — Promotion, rehearsal and capital deployment boundaries (12 tasks)

**Goal:** Prepare for real-money integration while leaving the final authorization under human control.

**Repository integration points:** `forward_edge/rehearsal.py`, `execution/oms/rollout.py`, `execution/oms/kite_broker.py`, `docs/RUNBOOK-LIVE.md`
- [ ] **PE-0207** — Define a frozen promotion policy specifying acceptable drawdown, cost stress, minimum coverage, statistical evidence and confidence bounds.  
  **Acceptance:** Policy is approved and hashed before observations enter the confirmatory cohort.
- [ ] **PE-0208** — Require eligible nonoverlapping forward-session count derived from sample-size design, not arbitrary trade count.  
  **Acceptance:** Promotion fails when session count is insufficient despite many correlated orders.
- [ ] **PE-0209** — Require observed prospective profitability above risk-matched benchmark after estimated executable costs.  
  **Acceptance:** A rising-market long book with negative alpha is not promoted.
- [ ] **PE-0210** — Require stronger robustness to 1.5x and 2x costs, missed fills and plausible adverse selection.  
  **Acceptance:** Cost-sensitive edge is shown as insufficient or failed.
- [ ] **PE-0211** — Require all risk incidents closed, ledger reconciled, data rights checked and custody verified.  
  **Acceptance:** Any critical unresolved anomaly blocks promotion.
- [ ] **PE-0212** — Provide read-only real-broker quote/status adapter and a separate mock broker for rehearsal.  
  **Acceptance:** Paper path cannot call order submission; dry-run calls are explicitly segregated.
- [ ] **PE-0213** — Run market-hours order-status reconciliation rehearsals without risking capital.  
  **Acceptance:** At least the policy-defined number of clean real-time sessions are recorded, not synthesized.
- [ ] **PE-0214** — Check venue/broker authorization, India-specific applicable retail algo rules and operational controls before physical order enablement.  
  **Acceptance:** Missing verified permissions keep LIVE_CASH disabled.
- [ ] **PE-0215** — Make micro-capital proposal state capital-at-risk, maximum daily loss, per-order limits, margin and emergency exit.  
  **Acceptance:** Only an owner-confirmed signed approval can advance rollout stage.
- [ ] **PE-0216** — Require observed broker fill/contract-note reconciliation before scaling any capital.  
  **Acceptance:** Scaled exposure blocked until fill-model error and risk limits pass review.
- [ ] **PE-0217** — Auto-disarm after unexpected order, stale feed, custody issue, abnormal cost, or ledger mismatch.  
  **Acceptance:** Re-arming requires owner action; an agent cannot reset it.
- [ ] **PE-0218** — If the candidate fails any promotion gate, preserve result as failure and continue shadow/research rather than optimizing the same forward cohort.  
  **Acceptance:** Failed trial remains immutable and no cash order is unlocked.

## 6. Minimum proof artifacts per real session

Every scheduled session should produce **all** of:

```
artifacts/forward_edge/YYYY-MM-DD/<cohort_id>/
  schedule-and-charter.json
  preflight.json
  source-capture-manifest.json
  normalized-market-events-manifest.json
  decisions.jsonl                # includes NO_SIGNAL/NO_TRADE/REJECTED
  manifests-and-custody.jsonl    # LOCAL_ONLY or independently VERIFIED
  risk-verdicts.jsonl
  order-intents.jsonl
  paper-orders-and-fills.jsonl
  cash-position-margin-ledger.jsonl
  exposure-and-open-marks.jsonl
  benchmark-and-controls.json
  cost-and-fill-fidelity.json
  incidents-and-missing.json
  daily-net-alpha.json
  session-completeness.json
  integrity-checksums.json
  operator-report.html
```

All paths are a proposed output contract, not a claim that these files already exist. Each artifact must have schema version, strategy/version hash, source hash, event/received/available times, created/closed status and traceable references. **A missing artifact is not zero return.**

## 7. Example command contract to implement

```bash
# Existing commands (fixture only at audited commit):
python -m financial_brain.forward_edge.audit --json
python -m financial_brain.forward_edge.cli preflight
python -m financial_brain.forward_edge.cli paper-run --fixture win
python -m financial_brain.forward_edge.cli report

# PROPOSED; do not pretend these currently exist:
fb edge sources --audit --no-orders
fb edge enroll --strategy candidate-v2 --protocol protocol.json --paper-only
fb edge record-session --date YYYY-MM-DD --cohort cohort-v1 --mode live-paper
fb edge recover-session --session-id S --read-only-reconcile
fb edge close-session --session-id S --verify-all
fb edge report --cohort cohort-v1 --include-missing --as-of YYYY-MM-DD
fb edge promotion-audit --cohort cohort-v1 --fail-closed
```

### Expected automated lifecycle for each permitted exchange day

**Premarket:** verify calendar, signed charter, strategy code/data hashes, quote rights, deterministic strategy state, capital/margin and prior cash/positions; fail closed on missing prerequisites.  
**Signal time:** consume only available data; emit sealed decision even when NO_TRADE; never create orders for SHADOW_ONLY.  
**Market hours:** archive quotes/depth, evaluate deterministic risk, simulate fills conservatively, record every reject/miss, persist accounting incrementally.  
**After close:** value cash and carry positions, reconcile open orders, publish cost/alpha/coverage, independently verify custody if configured, preserve incomplete-day classifications.  
**Frozen checkpoints only:** compute prospective verdict and harm stopping criteria; no daily parameter fitting or best-strategy hindsight promotion.

## 8. Profitability audit dashboard: exact metrics

| Metric | Computation | Why it matters |
|---|---|---|
| Daily net NAV return | Change in canonical NAV, flow-adjusted | Full economic outcome, not isolated closed trades |
| Gross P&L and cost drag | Gross trading P&L, spread, impact, levies, commissions and financing | Is edge real or a fee leak? |
| Net alpha to matched benchmark | Strategy net daily return minus frozen control net | Market beta and factor effects vs strategy contribution |
| Lower confidence bound on alpha | Registered dependence-robust estimator | Precision and statistical validity |
| Maximum drawdown and tail loss | Full NAV including open positions | Risk of ruin and capital suitability |
| Turnover / participation | Net actual holdings changes and executable volume | Capacity and fragile cost assumptions |
| Missed/partial fills | Intended vs eligible executed quantity | Avoid imaginary fills |
| Opportunity-weighted quote coverage | Eligible notional with valid source inputs | Whether result generalizes to planned universe |
| No-trade/invalid sessions | All scheduled sessions, with reasons | Prevention of selection bias |
| Rejected trade counterfactuals | Clearly marked, modeled and unpromotable | Diagnose missed opportunity without hindsight |
| Factor and regime decomposition | Match beta, size, volatility, momentum and dates | Distinguish novelty from known exposures |
| Active code, model and cost SHA | Frozen version manifest | Reproducibility and governance |
| Independent custody share | Verified decision receipts before earliest entry | Formal authenticity of prospectively recorded picks |

## 9. Mandatory adversarial end-to-end scenarios

1. A synthetic winning equity trade under conservative marketable-limit fills.
2. A synthetic losing trade whose exit costs exceed the original expected profit.
3. A long-running equity book loses on open positions while closed trades show gains; NAV still reports the loss.
4. NO_TRADE on an insufficient expected return net of costs.
5. A missing Kite bid/ask or expired quote prevents a fill, even when LTP moved favorably.
6. A strategy crashes after manifest written but before order acknowledgement; replay does not create or drop a fill.
7. Two strategies attempt to reserve the same available cash; only valid aggregate allocation proceeds.
8. An option spread partially fills one leg; no fabricated spread completion or undisclosed risk.
9. A futures contract rolls across expiry with fees and variation margin fully included.
10. A suspended/delisted equity has no close price; the result is NOT a favorable survivor-only portfolio.
11. Every scheduled date with a missing feed remains in the analysis denominator.
12. Tampering with a decision manifest invalidates its recorded receipt.
13. Force an apparent profitable strategy by using future data; leakage test rejects it.
14. Inflate strategy search from 2 to 200 variants; multiplicity gate becomes stricter, not easier.
15. Simulate profit from a 2026Q4 failed trial and try to auto-promote it; governance rejects the attempt.
16. Re-run a real recorded day after code change; old result remains immutable and new result is REPLAY/DIAGNOSTIC.
17. Cost multiplier doubles: net results degrade monotonically where no behavioral trade changes occur.
18. A weak candidate with 60 days and many trades fails promotion if effective sample is too small.
19. A valid negative result completes the cohort with honest `EDGE_NOT_CONFIRMED`, not a release failure.
20. Attempt a physical order from the paper CLI; the broker action is refused in all test environments.

## 10. Definition of done, without pretending profitability is guaranteed

- **Engineering complete:** All internally actionable tasks pass executable CI, failure injection, ledger reconciliation, session replay parity, live-source adapter tests, and a fresh authorized market-data capture. External blockers remain explicitly listed, never faked.
- **Paper-ready:** A complete exchange day can start, make NO_TRADE/trade decisions, simulate realistic orders, carry inventory, settle, and produce an auditable full-NAV report without manual spreadsheet repair.
- **Forward evidence credible:** Strategy, sample, benchmark, timestamps and test policies frozen in advance; actual market days accumulate; missing outcomes and data coverage reported; independent verification provided when claiming independent custody.
- **Positive edge confirmed:** Only if **actual prospective** net alpha clears the frozen risk-matched, multiplicity-aware, cost-stressed, statistical and operational gates. Failure is a valid final research outcome.
- **Micro-live candidate:** Only after confirmed edge, venue/broker/legal checks, market-hours rehearsal, validated broker reconciliation and explicit human-approved economic risk.

## 11. Codex/Coding-agent master instruction

> Work inside `crakashp2905-hub/Financial-Brain`, auditing latest checkout before changes. Use `PE-0001` onward as task identities. Implement one wave at a time, reusing existing modules; avoid new parallel paper engines. Do not rewrite protected trials, look at forbidden holdouts, modify a live cohort after observation, or submit real broker orders. Build all testable code in-house and locally with existing or permitted dependencies. Whenever a task needs source access, an owner decision or elapsed market time, implement its interface and negative mocks, mark `EXTERNAL_BLOCKED`, and continue to other independent tasks. Never fabricate an exchange quote, independent timestamp, fill, broker approval or successful statistical result. Maintain `docs/edge-program/task-status.json` with task ID, current state, source paths, test names, test exit codes, evidence SHA, last reviewed SHA and blocker. Require a green clean-clone CI matrix and explicit tested acceptance proof for each `DONE`. Prefer conservative execution, no-trade abstention, and exact-accounting integrity over apparent paper profitability. Once a complete observed session exists, preserve it as immutable evidence, then advance only under frozen, preregistered statistical criteria. End by producing a machine-readable release gate and current honest verdict; do not claim 10/10 or profitable edge without qualifying evidence.

## 12. Final strategic emphasis

The highest-value first change is **not another complex model**. It is using the strongest historical economic mechanisms (low-turnover momentum, 52-week-high, low volatility) as properly governed, separate research candidates **while wiring actual market quotes to reliable portfolio-level paper accounting**.

If the evidence says the expected alpha does not pay for cost and risk, **NO_TRADE is the correct outcome**. A durable system that refuses weak trades and learns from independent forward evidence is far closer to a professional trading operation than a paper engine showing artificially positive P&L.
