# FINANCIAL-BRAIN — Chart-Pattern Intelligence & Offline Trader Workstation
## Autonomous, code-only end-to-end implementation master checklist

**Specification date:** 2026-11-07  
**Scope:** turn the Nison/Bulkowski chart-pattern research, existing feature library, and paper-only Financial-Brain terminal into a **complete offline, trader-usable pattern intelligence product**, with deterministic simulations, governed learning, reproducible research tools, and selective Rust only when justified.  
**Task count:** **406 individually numbered engineering tasks in 27 workstreams** (310 P0, 85 P1, 11 P2).  
**Execution authority:** AI coding agent may implement all software, tests, fixtures, documentation, and local test evidence; **no live network feed, new API keys, paid data, live orders, outside expert or human approval required for the code-completion target.** The shipped demo must operate with networking disabled.

> **CRITICAL: 10/10 code quality and trader usability are goals, not guarantees, and cannot certify real profit or zero losses.** Real trading alpha, live-market paper performance, independent timestamp custody, data rights, and real-world brokerage fill calibration are intrinsically external or time-dependent. Do not fake them. The completion gate is **engineering/trader-ready in offline paper mode**, not `EDGE_CONFIRMED`, a live-order release, or guaranteed profitability.

### Source baseline and freshness

The last successfully inspected branch was `codex/source-notes-pattern-books` at `7244f186a679` (PR #18 open on 2026-11-07), based on `main` at `a2130ba81602`. It contained **three research Markdown notes and `docs/sources/pattern-catalogue.v1.json` (72 patterns: 25 Nison, 47 Bulkowski)**; PR #18 did **not** install geometric detectors or run new performance experiments. Earlier main CI passed **1,684 tests per OS** on selected critical coverage of 82%. During authoring, the GitHub connector intermittently returned 404; these values must be verified by the implementation agent before acting.

**Already implemented; reuse rather than rewrite:** `src/financial_brain/features/library.py` contains candle/technical features, `evaluation/chart_setups.py` contains K7 experiments, `evaluation/family_validation.py` provides family-aware validation, `research/preregistry.py` and `evaluation/standard.py` govern registered tests, `evaluation/champion.py` and `evaluation/prospective.py` enforce prospective evidence, `paper/engine.py`, `paper/live_view.py`, `paper/run_manifest.py`, `paper/execution_audit.py`, `execution/oms/` and `risk/` provide paper/OMS/risk foundation. Validate names and locations against the actual checkout.

**Existing null finding:** K7's 17 chart/candlestick future setups all failed the project's registered G3 gate. Do not erase, re-run as a new decisive trial, reinterpret as proven alpha, or tune definitions to obtain a positive result.

**Research budget:** the 2026Q4 preregistered trial budget is documented as **13/13 spent**. Do not run new real-data hypothesis tests on protected histories or use the final holdout; development here is with deterministic fixtures, contracts, static analysis, non-outcome diagnostics, and separately flagged sandbox demonstrations. No backdated forward evidence.

### Autonomous implementation rules

1. **Never request external input just to finish code**: choose safe, documented defaults and create fixtures, mocks and offline adapters. External evidence gaps are stated honestly and are not software blockers.
2. **Prioritize and ship in small waves**: one or several logically related workstreams per PR/commit, with passing relevant tests and acceptance artifacts. Preserve existing public APIs.
3. **Inspect before coding**: if a checklist task already exists, confirm behavior and add missing tests instead of duplicating it. Mark such tasks `verified_existing`, citing exact code and tests.
4. **No manufactured financial performance**: synthetic demonstrations must be labeled. Never turn `pattern found` into `buy` without independent evidence, and never claim `zero losses`.
5. **No unauthorized execution**: no Kite order calls, no API key collection, no live trading activation, no mutation of trial budget/holdout or historical financial evidence.
6. **No code-only escape hatch for external gates**: independent timestamps, market-time forward cohorts, exchange data rights and live broker calibration cannot be declared completed by mocks.
7. **Rust is optional**: only write native code if profiling + parity tests justify it. Prefer maintained Python/DuckDB integration.
8. **At each checkpoint**: run the local test suite, record commit and dataset hashes, produce a list of completed tasks, failures and blocked items, and proceed through remaining code-only tasks.

### Reference architecture

```text
Existing local lake / DuckDB / fixtures (readonly)
    -> PIT bar adapter + versioned calendar + lineage validation
    -> causal swing/pivot & geometry primitives
    -> versioned multi-pattern detectors
    -> immutable candidate -> confirmed/invalidated/expired event stream
    -> descriptive geometry & provenance -> trader radar + chart overlays
    -> research-only study protocol + benchmarks + placebo controls
    -> paper-only intent -> deterministic risk gate -> cost-aware simulator
    -> paper ledger / execution coverage / journal / offline viewer
    -> matured outcomes -> shadow learner -> audited proposal (never auto-live)
```

### Semantics that cannot be changed to improve results

- **Causal recognition:** an event computed at t must depend only on observations available by t. A centered swing pivot with `right_bars=2` is not observable until t+2.
- **Two clocks:** initial candidate recognition and confirmed breakout are different research populations; store both, and retain candidates that fail.
- **Realized net trading P&L:** cash ledger plus positions, actual eligible entry timing and explicit modeled costs; not book-reported ultimate favorable excursion.
- **Independent evidence:** local hashes and `datetime.now()` detect mutation but do not establish independent forward custody.
- **Evidence tier:** literature claims < research observations < historical replicated candidate < genuinely forward paper evidence < externally confirmed, deployable strategy. A pattern detector itself never raises evidence tier.
- **Conditional book stats:** Bulkowski-style breakout success is conditional on completed/confirmed cases and on the historical US sample; the chart catalogue must not convert it into Indian trade win probabilities.

### Core data contracts

```python
@dataclass(frozen=True)
class PatternSpec:
    pattern_id: str
    version: str
    timeframe: str
    parameters: dict  # frozen/canonicalized on serialization
    recognition_rule: str
    confirmation_rule: str
    invalidation_rule: str
    source_id: str

@dataclass(frozen=True)
class PatternEvent:
    event_id: str
    spec_sha256: str
    lineage: str
    timeframe: str
    candidate_known_at_utc: str
    confirmed_at_utc: str | None
    invalidated_at_utc: str | None
    earliest_entry_at_utc: str | None
    status: str  # CANDIDATE|CONFIRMED|INVALIDATED|EXPIRED|SUPERSEDED
    geometry: dict  # bars, pivots, trendlines, levels, provenance
    data_sha256: str
    evidence_tier: str  # always descriptive unless validated externally
```

**Invariant:** for any bars `B[:t]`, `detect(B) filtered to events known by t == detect(B[:t])`, allowing only new transitions with `known_at > t` on later bars. Older events cannot be silently rewritten.

### Mathematical and accounting checks

Fixed executable holding-period result, when data are sufficient:

\[R_{net} = \frac{V_{exit} - V_{entry} - C_{fees} - C_{spread} - C_{impact} - C_{financing}}{V_{entry}}\]

The paper ledger should reconcile on each observation time:

\[E_t = \text{cash}_t + \sum_i q_{i,t}P_{i,t} - \text{liabilities}_t\]

For candidate filtration, comparison must be incremental and matched:

\[\Delta R = R_{pattern+baseline, net} - R_{baseline, net}\]

All metrics must specify the denominator, return horizon, timing assumptions, eligible universe, dependence treatment and which costs are observed versus modeled. No inference from simulated edge-fixtures to actual market returns.

---

## Master implementation checklist

Each item is a **software task with a binary acceptance criterion**. Completion evidence must point to a code change or reviewed existing implementation **and a passing test / reproducible artifact**.

### 00 — Autonomous repository reconciliation  
**Priority:** P0 · **Purpose:** Start from verified code, not assumptions. Keep all development local and paper-only.  
**Primary targets:** `README.md, .github/workflows/, docs/audits/, pyproject.toml`

- [ ] **CP-0001** Fingerprint the checkout and write a machine-readable baseline with commit SHA, dirty state, Python version and dependency hashes.  
  **Done when:** `docs/audits/pattern-baseline.json` is produced deterministically with the current SHA and local environment.
- [ ] **CP-0002** Read main and all accessible chart-pattern branches; detect whether the research PR is merged or superseded.  
  **Done when:** A status table differentiates merged, unmerged, missing, and stale code; it never asserts inaccessible branches are present.
- [ ] **CP-0003** Inventory existing candle, technical-feature, geometry, chart UI, execution, risk and evaluation modules.  
  **Done when:** A source-to-capability matrix links each claimed feature to source and tests.
- [ ] **CP-0004** Compare the 72 pattern catalogue entries with existing code and tests without trusting `repo_status` strings as proof.  
  **Done when:** Every entry has an independently verified `implemented|partial|research_only|missing` label.
- [ ] **CP-0005** Protect historical trials, protected datasets, preregistrations, decision ledgers and existing hashes.  
  **Done when:** Diff-based regression test proves no protected record is modified.
- [ ] **CP-0006** Create an isolated feature branch/worktree for chart-pattern implementation.  
  **Done when:** Branch excludes credentials, local data and generated artifacts from version control.
- [ ] **CP-0007** Search for duplicate chart-pattern detectors in `features/library.py`, `evaluation/chart_setups.py` and other modules.  
  **Done when:** Design note names exactly what is reused vs newly implemented.
- [ ] **CP-0008** Record 2016–2026 and F&O data limitations as stated in the vault without inventing coverage.  
  **Done when:** Baseline report distinguishes installed datasets from optional or absent datasets.
- [ ] **CP-0009** Record an offline-only development policy: fixtures are allowed, fresh market evidence is not synthesized.  
  **Done when:** Any externally dependent gate is categorized `OWNER|DATA_TIME|LICENSE`, not marked finished.
- [ ] **CP-0010** Write a versioned plan-to-code mapping rather than recreating every subsystem.  
  **Done when:** `docs/audits/pattern-implementation-map.md` references actual files and test targets.
- [ ] **CP-0011** Establish `PATTERN_DEV_MODE=offline` and a centralized flag rejecting external/network/broker operations.  
  **Done when:** Test monkeypatches network and broker adapters to fail whenever invoked in offline mode.
- [ ] **CP-0012** Add a checkpoint command to re-run the baseline audit after every completed wave.  
  **Done when:** Re-running shows changed source, tests, coverage and unresolved items.

### 01 — Canonical machine-readable catalogue  
**Priority:** P0 · **Purpose:** Turn the Nison/Bulkowski research into validated hypotheses, never executable trading claims.  
**Primary targets:** `docs/sources/pattern-catalogue.v1.json; patterns/catalogue.py; tests/test_pattern_catalogue.py`

- [ ] **CP-0013** Introduce a JSON Schema for every catalogue entry, with mandatory keys by source and explicit optional fields.  
  **Done when:** Invalid or source-incompatible records fail schema validation.
- [ ] **CP-0014** Move free-form sample counts into numeric `sample_total`, `sample_confirmed`, `sample_unconfirmed` where extractable from existing notes.  
  **Done when:** Typed sample counts are nonnegative and retain the original textual claim for provenance.
- [ ] **CP-0015** Represent failure, direction, target-hit and average-move statistics with explicit denominator, measurement window and conditioning.  
  **Done when:** Every quantitative claim is marked as conditional, descriptive and historical.
- [ ] **CP-0016** Represent non-numeric or ambiguous historical claims as `null` plus `source_claim_text`.  
  **Done when:** No parser guesses a number from expressions such as `~`, `<`, or multiple variants.
- [ ] **CP-0017** Add source title, edition/year, chapter and `page_unverified` fields.  
  **Done when:** Missing precise references remain explicitly unknown rather than fabricated.
- [ ] **CP-0018** Validate every pattern ID for uniqueness and stable source prefixes.  
  **Done when:** Duplicate or changing IDs fail validation and migration tests.
- [ ] **CP-0019** Normalize families into a controlled taxonomy independent of the source's prose.  
  **Done when:** Every catalogue row maps to a documented family enumeration.
- [ ] **CP-0020** Separate `direction` from `claim` and `eligible_side` so descriptive reversals are not implied trades.  
  **Done when:** Each entry unambiguously distinguishes bullish, bearish, neutral and either-direction detection.
- [ ] **CP-0021** Add `definition_status` with `descriptive|formalized|implemented|fixture_verified|paper_eligible` values.  
  **Done when:** State transitions require evidence and cannot be set from text alone.
- [ ] **CP-0022** Add `parameters` and `knowledge_lag` slots to every entry, initially unset unless precisely specified.  
  **Done when:** Undefined thresholds cannot silently fall back to fitted defaults.
- [ ] **CP-0023** Add `source_selection_bias`, `lookahead_risk`, `source_market`, `source_period` fields.  
  **Done when:** Book results display their sample selection and geography in API output.
- [ ] **CP-0024** Version and migrate catalogues without changing the meaning of existing stable IDs.  
  **Done when:** Migrating v1 to v2 preserves old records and produces a deterministic hash.
- [ ] **CP-0025** Provide a CLI command to validate, count and print catalogue completeness by category.  
  **Done when:** Tool exits nonzero on malformed data and reports 72 input entries when supplied with the source catalogue.
- [ ] **CP-0026** Generate a reference index mapping source notes, catalogue IDs and detector implementations.  
  **Done when:** Every implementation has a traceable source identifier.

### 02 — Pattern contract, types and provenance  
**Priority:** P0 · **Purpose:** Specify exactly what the system knows, when it knows it, and what a pattern can justify.  
**Primary targets:** `src/financial_brain/patterns/{spec,models,registry,provenance}.py`

- [ ] **CP-0027** Define immutable `PatternSpec` with ID, version, family, interval, causal parameters, signal rules and de-duplication.  
  **Done when:** Serializing the same spec yields identical bytes and SHA-256 on all supported platforms.
- [ ] **CP-0028** Define `PatternEvent` with security lineage, interval, formation window, recognition, confirmation, invalidation and state.  
  **Done when:** Required fields have explicit types and valid timestamp ordering.
- [ ] **CP-0029** Use explicit event clocks `formation_start`, `candidate_known_at`, `confirmed_at`, `decision_at`, `earliest_entry_at`.  
  **Done when:** No signal is executable earlier than its last required observable bar.
- [ ] **CP-0030** Define state enum `CANDIDATE`, `CONFIRMED`, `INVALIDATED`, `EXPIRED` and `SUPERSEDED`.  
  **Done when:** Invalid transitions raise typed errors.
- [ ] **CP-0031** Encode geometry using bar IDs, price levels and time coordinates, not mutable chart pixels.  
  **Done when:** Pattern geometry is replayable independently of UI resolution.
- [ ] **CP-0032** Represent `direction` and `side` independently so bearish observations may mean exit-only.  
  **Done when:** Forbidden short exposure cannot be inferred from descriptive text.
- [ ] **CP-0033** Define `EvidenceTier` separate from detector confidence and any estimated win probability.  
  **Done when:** A detected shape cannot label itself `EDGE_CONFIRMED`.
- [ ] **CP-0034** Provide stable event identities based on lineage, timeframe, detector version and first-known date.  
  **Done when:** Rerunning on the same data neither duplicates nor renames prior events.
- [ ] **CP-0035** Define model-level provenance for catalogue version, source OHLC hash, code commit, calendar and config.  
  **Done when:** Every result can reproduce its exact inputs and code.
- [ ] **CP-0036** Define explicit recognition delays for pivots, completed candles, weekly bars and market close.  
  **Done when:** Delay is enforced by the engine, not merely noted in documentation.
- [ ] **CP-0037** Require frozen configuration hashes for each event stream.  
  **Done when:** A parameter change creates a new version instead of rewriting old results.
- [ ] **CP-0038** Implement strict numeric validation: finite prices, bounded probabilities, legal timestamps and nonnegative durations.  
  **Done when:** NaN, inf and malformed boundaries fail closed.
- [ ] **CP-0039** Define `PatternSignal` as optional research output, distinct from an `OrderIntent`.  
  **Done when:** No pattern module can submit an OMS order or import a broker client.
- [ ] **CP-0040** Add canonical JSON encoding and migration tests for all public model types.  
  **Done when:** Round-trips preserve identity, ordering and Decimal values.

### 03 — Market data, calendars and security identity  
**Priority:** P0 · **Purpose:** Ensure input history is internally coherent and observable as of the decision.  
**Primary targets:** `src/financial_brain/patterns/data.py; ingest/quality.py; pit/observations.py`

- [ ] **CP-0041** Create a read-only `BarSource` interface using the project's existing DuckDB and Parquet data.  
  **Done when:** Detectors do not fetch remote data or mutate research stores.
- [ ] **CP-0042** Validate OHLC inequalities, positive prices, finite volume and strictly increasing bar sessions.  
  **Done when:** Malformed bars are quarantined and visibly counted.
- [ ] **CP-0043** Normalize timezones to aware Asia/Kolkata exchange timestamps and UTC persistence.  
  **Done when:** Daylight and timezone tests reject naive timestamps.
- [ ] **CP-0044** Source session calendars from versioned available exchange-session tables, not weekday arithmetic.  
  **Done when:** Holiday and special-session fixtures produce correct schedules.
- [ ] **CP-0045** Implement an offline calendar fixture with holidays, shortened sessions, circuits and suspension gaps.  
  **Done when:** Test suite runs without an exchange API.
- [ ] **CP-0046** Enforce canonical lineage/ISIN identifiers across ticker renames and duplicated aliases.  
  **Done when:** Each event maps to one valid security identity or is withheld.
- [ ] **CP-0047** Document adjusted-versus-unadjusted OHLC conventions for geometric detection and execution.  
  **Done when:** Corporate-action adjustments cannot leak future information into prior recognition.
- [ ] **CP-0048** Build total-return and split-aware feature fixtures with ex-dates and historic prices.  
  **Done when:** Pattern geometry survives split adjustment without creating fictional breakouts.
- [ ] **CP-0049** Add immutable input snapshots with row count, min/max session and SHA-256.  
  **Done when:** Replays verify the same byte-equivalent data contract.
- [ ] **CP-0050** Disallow volume-driven rules if the data feed has missing or suspicious volume.  
  **Done when:** Event becomes ineligible or signals an explicit `DATA_INSUFFICIENT` reason.
- [ ] **CP-0051** Disallow unsupported shorting in cash equities; restrict paper shorts to explicitly supported futures fixtures.  
  **Done when:** Segment eligibility is tested per instrument.
- [ ] **CP-0052** Separate EOD-only data from intraday quote/depth and executable order-book evidence.  
  **Done when:** UI labels timing and fill observability distinctly.
- [ ] **CP-0053** Provide deterministic handling for absent, halted and illiquid sessions.  
  **Done when:** No forward-filled traded bars are silently manufactured.
- [ ] **CP-0054** Build data-freshness and staleness diagnostics for every requested symbol and interval.  
  **Done when:** Detection can abstain with actionable, testable error messages.
- [ ] **CP-0055** Create a dataset entitlement matrix, with unavailable datasets represented by optional adapters.  
  **Done when:** No test requires external quotes, licenses or account credentials.

### 04 — Causal swing points and geometric primitives  
**Priority:** P0 · **Purpose:** Create the foundation for non-repainting multi-bar pattern detection.  
**Primary targets:** `src/financial_brain/patterns/{pivots,geometry,segments}.py`

- [ ] **CP-0056** Implement causal confirmed swing highs and lows with configurable left/right observation windows.  
  **Done when:** Pivot is emitted only after all required right bars have actually arrived.
- [ ] **CP-0057** Implement ties and flat tops using deterministic index and price tolerance rules.  
  **Done when:** Synthetic repeated highs/lows yield stable pivots.
- [ ] **CP-0058** Implement ATR-normalized, percentage and tick-aware level proximity as separate explicit policies.  
  **Done when:** No detector mixes tolerance units or tunes them on performance.
- [ ] **CP-0059** Implement robust trendline fitting with two-point and predeclared robust-regression alternatives.  
  **Done when:** Lines only reference pivots already known by the detection time.
- [ ] **CP-0060** Implement horizontal support and resistance bands with observed touch counts.  
  **Done when:** A breakout level never uses future highs or lows.
- [ ] **CP-0061** Implement line intersections, convergence, divergence, angle and slope-in-time tests.  
  **Done when:** Vertical or degenerate configurations fail cleanly.
- [ ] **CP-0062** Implement neckline and intermediate-peak/valley construction from causally known pivots.  
  **Done when:** Shapes with undefined necklines are rejected.
- [ ] **CP-0063** Implement candidate bounding boxes and duration limits in exchange sessions.  
  **Done when:** Detection length is invariant to missing weekends and holidays.
- [ ] **CP-0064** Implement configurable trend context: return, fitted slope and high/low sequence, all on past-only windows.  
  **Done when:** Context label is calculable from prefix history alone.
- [ ] **CP-0065** Implement session-aware volume contraction/expansion statistics.  
  **Done when:** Missing volume produces unknown rather than zero.
- [ ] **CP-0066** Implement compression, range-width, pole-height and retracement primitives in consistent price fractions.  
  **Done when:** Values are bounded or explicitly marked invalid.
- [ ] **CP-0067** Implement intrabar OHLC ambiguity detector for competing breakout/stop/target levels.  
  **Done when:** Ambiguous ordering never resolves optimistically.
- [ ] **CP-0068** Implement geometry de-duplication by pivots and overlapping windows.  
  **Done when:** Same visual shape cannot generate unlimited duplicate trials.
- [ ] **CP-0069** Implement deterministic event ordering and stable tie-breaking across simultaneous candidates.  
  **Done when:** Result is independent of input row ordering after canonical sort.
- [ ] **CP-0070** Implement stream-based incremental updates that consume one completed bar at a time.  
  **Done when:** Incremental events match batch prefix output exactly.
- [ ] **CP-0071** Implement bounded-memory lookback windows without deleting still-active candidates.  
  **Done when:** Memory footprint is capped and event identities remain stable.
- [ ] **CP-0072** Add zero-division, one-bar, constant-price, spike, missing-bar and split tests.  
  **Done when:** Primitives never emit NaN or unstable results.
- [ ] **CP-0073** Add a serializable geometric debug trace for each acceptance/rejection decision.  
  **Done when:** Each detector can explain which threshold passed or failed.

### 05 — Range and continuation detectors  
**Priority:** P0 · **Purpose:** Implement high-priority, economically interpretable breakout shapes first.  
**Primary targets:** `src/financial_brain/patterns/detectors/{range,triangles,flags,wedges,cup}.py`

- [ ] **CP-0074** Implement rectangle-top candidate formation with flat resistance and bounded congestion.  
  **Done when:** Tests include clear positive, near-miss and false-positive bars.
- [ ] **CP-0075** Implement rectangle-bottom candidate and separate upside/downside confirmed breakouts.  
  **Done when:** Direction never inferred before breakout confirmation.
- [ ] **CP-0076** Implement ascending-triangle detector with flat upper boundary and rising known swing lows.  
  **Done when:** Detection requires precommitted touch counts and minimum duration.
- [ ] **CP-0077** Implement descending-triangle detector with flat lower boundary and falling known swing highs.  
  **Done when:** Opposite-direction breakouts are recorded rather than discarded.
- [ ] **CP-0078** Implement symmetrical triangle with converging upper/lower slopes.  
  **Done when:** Detector rejects parallel, diverging and intersected-too-early cases.
- [ ] **CP-0079** Implement support/resistance breakout confirmation at bar close with optional delayed re-test rule.  
  **Done when:** Signals are known only after confirmation bar closes.
- [ ] **CP-0080** Implement bullish and bearish flag candidates with predeclared pole, retracement and short duration.  
  **Done when:** Pole and flag cannot overlap future bars.
- [ ] **CP-0081** Implement pennant candidates with tapering swing boundaries and bounded duration.  
  **Done when:** Detector distinguishes pennant from short symmetric triangle by fixed criteria.
- [ ] **CP-0082** Implement ascending and descending wedge geometry with slope and convergence constraints.  
  **Done when:** Spurious fits with low explanatory quality are rejected.
- [ ] **CP-0083** Implement rising and falling channel/continuation families without treating any trendline as proof of an edge.  
  **Done when:** Research-only events carry descriptive confidence.
- [ ] **CP-0084** Implement causal cup-with-handle detector: left lip, cup low, right lip, handle and break.  
  **Done when:** Handle must be recognized before the breakout; no retrospectively fitted cup.
- [ ] **CP-0085** Implement high-tight-flag candidate with historical-only high-rise precondition and fixed flag-length limits.  
  **Done when:** Detector handles extreme spikes and splits safely.
- [ ] **CP-0086** Implement alternate breakout directions and failed-breakout events for each range family.  
  **Done when:** Failure labels only appear after actual failure is observed.
- [ ] **CP-0087** Implement a no-new-data streaming fixture covering all basic continuation patterns.  
  **Done when:** Only completed bar data drives detection.
- [ ] **CP-0088** Implement detector-specific geometry visual fixtures saved as small JSON, not source images.  
  **Done when:** QA can render exact boundaries from serializable coordinates.
- [ ] **CP-0089** Implement cross-pattern deduplication where rectangles, triangles and flags refer to the same underlying consolidation.  
  **Done when:** An explicit precedence policy avoids duplicate trading suggestions.
- [ ] **CP-0090** Implement a single batch discovery interface returning all candidate, confirmed and failed formations.  
  **Done when:** Caller can filter without recomputing independent detectors.

### 06 — Reversal and complex geometric detectors  
**Priority:** P1 · **Purpose:** Build additional catalogue coverage as descriptive research, with prioritization by testability.  
**Primary targets:** `src/financial_brain/patterns/detectors/{double_extrema,head_shoulders,rounding,complex}.py`

- [ ] **CP-0091** Implement tentative double-bottom formation with two past-observed lows and mid-peak.  
  **Done when:** All lookalikes, including never-confirmed, remain in event history.
- [ ] **CP-0092** Implement confirmed double-bottom break over causal intermediate peak.  
  **Done when:** Recognition and confirmation are separated and timestamped.
- [ ] **CP-0093** Implement tentative and confirmed double-top mirror detector.  
  **Done when:** No bearish success statistic is calculated on confirmed-only sample by default.
- [ ] **CP-0094** Implement triple-bottom and triple-top candidate sequences with fixed symmetry and duration.  
  **Done when:** Extra extrema do not silently change original event identity.
- [ ] **CP-0095** Implement head-and-shoulders bottom with prior downtrend, symmetric shoulders and a causal neckline.  
  **Done when:** Signal cannot appear before right shoulder is known.
- [ ] **CP-0096** Implement head-and-shoulders top with prior uptrend and inverse neckline logic.  
  **Done when:** Historical completed-chart fits never overwrite earlier observations.
- [ ] **CP-0097** Implement rounding-bottom and rounding-top geometry using a predeclared smoothness/curvature criterion.  
  **Done when:** Synthetic trends, sawtooth false positives and round shapes discriminate consistently.
- [ ] **CP-0098** Implement bump-and-run bottom candidate and confirmed trendline breach.  
  **Done when:** Angles normalized for time/price scaling are not raw pixel angles.
- [ ] **CP-0099** Implement bump-and-run top with causal acceleration and reversal definition.  
  **Done when:** No future ultimate low is used in forming the signal.
- [ ] **CP-0100** Implement broadening-top/bottom bands from diverging known swing points.  
  **Done when:** Insufficient touches or nondivergence reject deterministically.
- [ ] **CP-0101** Implement diamond-style widening then narrowing candidate, with minimum evidence requirements.  
  **Done when:** Requires both phases known before confirmation.
- [ ] **CP-0102** Implement island reversal from completed, non-overlapping range gaps.  
  **Done when:** Detector records gap fills and corporate-action exclusions.
- [ ] **CP-0103** Implement optional horn, pipe and weekly spike patterns as documented low-priority detectors.  
  **Done when:** Rare events trigger `INSUFFICIENT_SAMPLE` not optimistic expected alpha.
- [ ] **CP-0104** Implement separate throwback/pullback states after confirmed breakouts.  
  **Done when:** Retest signals are causal and never treated as guaranteed.
- [ ] **CP-0105** Implement neckline, target and invalidation levels for explanatory overlays, not fitted profit targets.  
  **Done when:** UI labels them descriptive and exposes chosen rule version.
- [ ] **CP-0106** Implement complexity caps to reject combinations with too many free geometry parameters.  
  **Done when:** All tunable parameters are enumerated and hashable.
- [ ] **CP-0107** Implement negative fixtures showing that a vaguely similar formation is not sufficient.  
  **Done when:** False positive examples are retained as regression cases.

### 07 — Candlesticks, gaps and weekly context  
**Priority:** P1 · **Purpose:** Reuse existing candle features; formalize missing cases only where rules can be frozen.  
**Primary targets:** `features/library.py; patterns/detectors/{candles,gaps,weekly}.py`

- [ ] **CP-0108** Inventory existing c_* features and map each to the exact catalogue ID and definition.  
  **Done when:** No duplicate implementation exists without an explicit compatibility note.
- [ ] **CP-0109** Wrap existing hammer, engulfing, star, marubozu and doji signals as timed `PatternEvent` outputs.  
  **Done when:** Same event counts and dates match existing features on fixtures.
- [ ] **CP-0110** Distinguish prior trend context from candle shape for hammer, hanging man and shooting star.  
  **Done when:** Mid-range patterns are tagged neutral/none where required.
- [ ] **CP-0111** Implement body gaps and whole-range gaps as separate detection primitives.  
  **Done when:** Nison window is not confused with an ordinary body gap.
- [ ] **CP-0112** Implement causally confirmed dark-cloud, piercing, harami and tweezers variants using frozen thresholds.  
  **Done when:** Confirmation semantics match documented spec and test fixtures.
- [ ] **CP-0113** Implement rising/falling three-methods continuation with strict bar ordering.  
  **Done when:** No event emits before the last confirming candle.
- [ ] **CP-0114** Implement tasuki, gap plays and separating lines only when full OHLC specification is available.  
  **Done when:** Ambiguous text remains `definition_pending` and cannot enter inference.
- [ ] **CP-0115** Implement support/resistance confluence as an observational feature rather than a trading multiplier.  
  **Done when:** No ad hoc probability boost or P&L promise.
- [ ] **CP-0116** Implement failed gap and gap-fill outcomes with future labels stored only after maturity.  
  **Done when:** Gap outcome never enters pre-decision features.
- [ ] **CP-0117** Implement weekly bar aggregation from completed exchange sessions and explicit week-close timestamps.  
  **Done when:** Partial week cannot masquerade as final weekly signal.
- [ ] **CP-0118** Implement multi-timeframe alignment without seeing a later intraday or daily closing bar.  
  **Done when:** Known-at timestamps are monotonically nondecreasing.
- [ ] **CP-0119** Preserve existing rejected K7 futures candle findings and their methodology in output metadata.  
  **Done when:** Terminal visibly warns that prior tested family failed its preregistered gate.
- [ ] **CP-0120** Write tests for gap after corporate action, circuit freeze, absent session and delayed open.  
  **Done when:** No incorrect breakout or automatic fill is emitted.

### 08 — Event lifecycle, replay and auditing  
**Priority:** P0 · **Purpose:** Make events reproducible and safe for both retrospective debugging and prospective shadow use.  
**Primary targets:** `src/financial_brain/patterns/{engine,lifecycle,events,store}.py`

- [ ] **CP-0121** Build append-only candidate event stream with new state transitions instead of in-place replacement.  
  **Done when:** Querying history reconstructs each prior visible state.
- [ ] **CP-0122** Emit `CANDIDATE` when minimum geometry becomes knowable, even if confirmation never follows.  
  **Done when:** Confirmed-only sampling bias is measurable.
- [ ] **CP-0123** Emit `CONFIRMED` only when fixed close or retest rules are met.  
  **Done when:** Confirmation time is strictly after or equal to earliest candidate knowledge.
- [ ] **CP-0124** Record invalidation and expiry timestamps with explicit reasons.  
  **Done when:** Unconfirmed failures are retained for denominator accounting.
- [ ] **CP-0125** Represent overlapping formations on one symbol with explicit parent/child or conflict links.  
  **Done when:** Reports can avoid accidental independent-count inflation.
- [ ] **CP-0126** Provide replay API with `as_of` filtering and immutable event histories.  
  **Done when:** Running a historical prefix returns only events then knowable.
- [ ] **CP-0127** Separate source observation time from computed event generation time.  
  **Done when:** A late replay does not claim prospective real-time provenance.
- [ ] **CP-0128** Reject duplicate event IDs with conflicting payload hashes.  
  **Done when:** Replay is idempotent, conflicting writes fail closed.
- [ ] **CP-0129** Implement event version migrations with backwards-compatible reader.  
  **Done when:** Old snapshots remain read-only and interpretable.
- [ ] **CP-0130** Provide event-state metrics: tentative count, confirmed count, invalidated count and time-to-confirm.  
  **Done when:** Rates use explicit at-risk denominators.
- [ ] **CP-0131** Make scan interruption and restart resume at last committed bar.  
  **Done when:** Crash-replay produces the same events as uninterrupted processing.
- [ ] **CP-0132** Implement consistent event ordering on same session for multiple detectors.  
  **Done when:** Order is independent of Python dictionary iteration.
- [ ] **CP-0133** Persist traceable detection reasons and thresholds with each event.  
  **Done when:** Arbitrary samples can be manually inspected without rerunning the engine.
- [ ] **CP-0134** Add a read-only event export in Parquet/JSON and SQL view over existing data.  
  **Done when:** Export is deterministic and never contains broker secrets.
- [ ] **CP-0135** Add an audit comparing candidate-count changes between detector versions.  
  **Done when:** Version changes never silently rewrite historical frequencies.

### 09 — Causality, correctness and adversarial tests  
**Priority:** P0 · **Purpose:** Prove pattern recognition does not peek forward or repaint.  
**Primary targets:** `tests/patterns/test_*; scripts/check_pattern_causality.py`

- [ ] **CP-0136** Add prefix-invariance property test: output at t equals full-run output restricted to t.  
  **Done when:** Thousands of generated sequences pass under fixed seeds.
- [ ] **CP-0137** Add future-perturbation test: append large future rally/crash without altering earlier events.  
  **Done when:** Earlier IDs, times, geometry and states are unchanged.
- [ ] **CP-0138** Add pivot-known-at test for every right-hand confirmation lag.  
  **Done when:** No pivot appears until its required confirmation window closes.
- [ ] **CP-0139** Add strict cutoff test for weekly/daily/intraday mixed bars.  
  **Done when:** Incomplete higher-timeframe candles cannot enter signals.
- [ ] **CP-0140** Add candidate-vs-confirmation test with unconfirmed lookalikes.  
  **Done when:** Metrics include failed and never-confirmed candidates.
- [ ] **CP-0141** Add trade-time causality test enforcing decision <= entry <= exit.  
  **Done when:** Violation rejects the event before paper adapter.
- [ ] **CP-0142** Add split, merger, symbol rename and dividend fixtures.  
  **Done when:** Identity and adjustment rules do not create false geometry.
- [ ] **CP-0143** Add volatility regime adversarial fixtures: flat, noisy, gapping, low-volume and discontinuous series.  
  **Done when:** Detectors do not crash or overclaim.
- [ ] **CP-0144** Add high/low/open/close contradiction fuzzing for ill-formed bars.  
  **Done when:** All malformed data routes to explicit quarantine.
- [ ] **CP-0145** Add detector restart, serialization and round-trip tests.  
  **Done when:** Incremental and replay event stores remain consistent.
- [ ] **CP-0146** Add inter-detector ambiguity tests (triangle vs flag vs rectangle).  
  **Done when:** Predeclared overlap policy produces stable output.
- [ ] **CP-0147** Add rare-pattern small-sample reporting tests.  
  **Done when:** Reports show low confidence and never derive probabilities from tiny counts.
- [ ] **CP-0148** Add seed and numerical tolerance portability tests for Windows/Linux.  
  **Done when:** Event hashes match across supported platforms.
- [ ] **CP-0149** Add negative tests proving no detector imports trading broker, network, or model tool actions.  
  **Done when:** Static boundary checker fails on prohibited imports.
- [ ] **CP-0150** Set minimum critical pattern-contract coverage and failure-path branch thresholds in CI.  
  **Done when:** Both threshold and exceptions are documented, reviewed and enforced.
- [ ] **CP-0151** Add mutation tests for pivot, breakout, calendar and event-time comparisons.  
  **Done when:** Deliberately broken comparisons cause test failures.
- [ ] **CP-0152** Add golden synthetic chart cases with expected coordinates and event history.  
  **Done when:** Visual and computation layers share a deterministic oracle.
- [ ] **CP-0153** Add database readonly and no-modification test on protected historical tables.  
  **Done when:** Data and trial ledger hashes remain unchanged after test suite.

### 10 — Offline research and study protocol  
**Priority:** P0 · **Purpose:** Create an implementable study runner without spending a new protected trial.  
**Primary targets:** `src/financial_brain/patterns/{study,protocol,diagnostics}.py; evaluation/*`

- [ ] **CP-0154** Create immutable `PatternStudySpec` describing family, versions, periods, matched controls, entries, exits and costs.  
  **Done when:** SHA-256 captures every parameter that could change a result.
- [ ] **CP-0155** Encode research family as one collective hypothesis rather than 72 independent claims.  
  **Done when:** Multiplicity accounting includes all examined pattern variants.
- [ ] **CP-0156** Define valid universe using historical point-in-time eligibility and F&O membership.  
  **Done when:** Current survivors never form the historical opportunity universe by default.
- [ ] **CP-0157** Create descriptive-only event census mode that computes no forward returns.  
  **Done when:** Can run now without consuming a trial or holdout.
- [ ] **CP-0158** Define executable next-open entry and next eligible-session mark rules.  
  **Done when:** Earlier same-close or optimistic OHLC entry is rejected.
- [ ] **CP-0159** Define fixed exits at 5, 20, 60 sessions, plus optional predeclared stop policy as separate strategy.  
  **Done when:** No ultimate-high/low exit permitted.
- [ ] **CP-0160** Provide candidate-clock and confirmation-clock panels with aligned at-risk denominators.  
  **Done when:** Both clocks show attempt counts and missing outcomes.
- [ ] **CP-0161** Record unconfirmed candidate outcomes separately from confirmed breakout returns.  
  **Done when:** Analysts cannot quote confirmed conditional win rate as setup success probability.
- [ ] **CP-0162** Add strict evidence tier and trial-budget checks before P&L evaluation on protected data.  
  **Done when:** 2026Q4 remains locked; no automatic new trial registration.
- [ ] **CP-0163** Add dry-run trial report that validates spec, sample coverage and cost model without computing returns.  
  **Done when:** Researchers see exactly what would be tested.
- [ ] **CP-0164** Build missing-outcome sensitivity policies for delistings, suspensions and missing close.  
  **Done when:** Incomplete cases never disappear silently.
- [ ] **CP-0165** Add blocked calendar-time and clustered-by-symbol confidence estimation routines.  
  **Done when:** Tests validate interval construction on correlated synthetic event clusters.
- [ ] **CP-0166** Reuse existing White Reality Check / SPA / CSCV-PBO where supported rather than reimplementing.  
  **Done when:** Module-level adapter invokes approved evaluation implementations.
- [ ] **CP-0167** Implement data-snooping audit that lists every pattern, direction, horizon and threshold variant.  
  **Done when:** Trial family size reconciles with specification.
- [ ] **CP-0168** Set up a frozen benchmark specification for naive momentum, 52-week-high proximity, low volatility and cash.  
  **Done when:** All comparisons use identical dates, universes and cost assumptions.
- [ ] **CP-0169** Prohibit backfilling pre-registered future observations into prospective ledgers.  
  **Done when:** Audit test rejects retroactive classification as forward evidence.
- [ ] **CP-0170** Provide runnable synthetic null and synthetic known-edge study examples.  
  **Done when:** Tests distinguish correct null from injected edge without touching market holdout.
- [ ] **CP-0171** Create provenance-rich report format including statistic, denominator, benchmark, eligibility, caveats and next proof gate.  
  **Done when:** No statistical output omits the input lineage and test family.

### 11 — Controls, attribution and incremental edge  
**Priority:** P0 · **Purpose:** Avoid mistaking trend continuation, volatility, selection or costs for novel chart-pattern alpha.  
**Primary targets:** `src/financial_brain/patterns/{controls,attribution,comparison}.py`

- [ ] **CP-0172** Implement matched controls by session, security group, momentum decile, 52-week-high position and volatility.  
  **Done when:** Match quality and unmatched cases are reported.
- [ ] **CP-0173** Add liquidity, size and sector controls where point-in-time attributes are available.  
  **Done when:** Missing attributes cause explicit lower-confidence tiers.
- [ ] **CP-0174** Implement within-name time-shift placebo using only eligible predeclared dates.  
  **Done when:** Placebo sampling is reproducible and sample-balanced.
- [ ] **CP-0175** Implement shuffled-direction and random-boundary pseudo-pattern controls.  
  **Done when:** A detector that beats no placebo cannot be upgraded.
- [ ] **CP-0176** Build matched baseline portfolios from the exact same candidate opportunity set.  
  **Done when:** Pattern filter's apparent edge cannot come solely from dropping hard-to-trade names.
- [ ] **CP-0177** Construct pattern-only vs momentum-only vs combined model comparisons.  
  **Done when:** Incremental value is measured on common decisions and fills.
- [ ] **CP-0178** Report exposure-neutralized residual outcomes and factor loadings for each family.  
  **Done when:** Residual alpha cannot be confused with raw market beta.
- [ ] **CP-0179** Add ablation toggles for geometry, volume, location, trend context and confirmation.  
  **Done when:** Contribution estimates acknowledge multiplicity and parameter count.
- [ ] **CP-0180** Implement nested chronological splits with a purge/embargo >= label overlap.  
  **Done when:** Test verifies training features do not see evaluation targets.
- [ ] **CP-0181** Measure turnover and capacity-adjusted cost deltas attributable to pattern filters.  
  **Done when:** An improvement is not claimed until net incremental benefit is measured.
- [ ] **CP-0182** Add no-trade/selective filtering as a first-class baseline policy.  
  **Done when:** Abstention is valued for realized risk reduction, not signal count.
- [ ] **CP-0183** Implement sensitivity stress matrix for ±spread, impact, delay and partial-fill assumptions.  
  **Done when:** Attractive results disappear visibly under adverse fill settings when appropriate.
- [ ] **CP-0184** Add family-level false discovery and minimum effect size gating.  
  **Done when:** No single noisy member can override family failure.
- [ ] **CP-0185** Create synthetic portfolio cases where adding patterns hurts and improves returns.  
  **Done when:** Comparison logic correctly rejects harmful overlays.
- [ ] **CP-0186** Implement pre/post breakdown of selected vs rejected names and opportunity cost.  
  **Done when:** Filtering false negatives are visible in research reports.
- [ ] **CP-0187** Add signed forecast-calibration metrics only for genuinely labeled data.  
  **Done when:** Historical selection studies are not presented as calibrated trading probabilities.
- [ ] **CP-0188** Implement counterfactual attribution separating entry, exit, sizing and pattern effect.  
  **Done when:** Aggregate outcome can be decomposed without double counting.

### 12 — Realistic paper entry, exits and capital  
**Priority:** P0 · **Purpose:** Provide working paper integration even with zero strategy approvals.  
**Primary targets:** `src/financial_brain/patterns/paper_adapter.py; paper/{engine,run_manifest,execution_audit}.py`

- [ ] **CP-0189** Build research-only `PatternIntent` that cannot carry live broker authorization.  
  **Done when:** Adapter can only target paper execution interfaces.
- [ ] **CP-0190** Enforce next executable session/bar and price reference after confirmation.  
  **Done when:** No same-bar close fill for completed-bar patterns.
- [ ] **CP-0191** Reconcile entry prices against allowed data tier: daily bars vs intraday quotes vs observed two-sided markets.  
  **Done when:** Simulated fills are labeled as assumptions.
- [ ] **CP-0192** Implement spread, fee, impact, price-band and lot-size cost breakdown by instrument.  
  **Done when:** Net value equals gross minus all modeled charges.
- [ ] **CP-0193** Support no-fill, partial-fill, rejection, timeout and stale-quote outcomes.  
  **Done when:** Equity and trade journals reflect each case without silently filling it.
- [ ] **CP-0194** Handle cash reservation, available buying power and cost-before-entry validation.  
  **Done when:** Paper account never has unexplained negative cash or hidden leverage.
- [ ] **CP-0195** Implement deterministic risk limits for max symbol, sector, correlation, leverage and total drawdown.  
  **Done when:** Violating an active limit prevents new paper intent.
- [ ] **CP-0196** Respect circuits, auctions, special sessions, suspensions and expiry eligibility.  
  **Done when:** No synthetic executable trade occurs while market is ineligible.
- [ ] **CP-0197** Create explicit closing/marking logic for unfilled orders and expired opportunities.  
  **Done when:** Pending intents do not become phantom holdings.
- [ ] **CP-0198** Implement stop/target ambiguity as pessimistic or explicitly unresolved when same OHLC bar spans both.  
  **Done when:** No optimistic assumption of favorable intrabar order.
- [ ] **CP-0199** Handle corporate actions and security lineage in open paper holdings.  
  **Done when:** Split/dividend effects reconcile without generating free profit.
- [ ] **CP-0200** Support capital-preserving abstention and empty candidate days.  
  **Done when:** Zero-trade sessions show zero realized P&L, not a successful strategy.
- [ ] **CP-0201** Link paper trade to source `PatternEvent`, `PatternSpec`, data and executable run manifest hashes.  
  **Done when:** Every simulated fill traces to a frozen detection decision.
- [ ] **CP-0202** Reconcile opening cash + cash flows + realized + unrealized = ending equity within numerical tolerance.  
  **Done when:** Property-based bookkeeping tests pass.
- [ ] **CP-0203** Compute trade-level and portfolio-level drawdowns, exposure, turnover, hit rate and profit factor.  
  **Done when:** Reports distinguish open P&L and completed trade P&L.
- [ ] **CP-0204** Report fill coverage and execution-source coverage per strategy and day.  
  **Done when:** Assumed fills never display as observed broker matches.

### 13 — Risk engine, no-trade and trader safeguards  
**Priority:** P0 · **Purpose:** Prevent a catalogued picture from becoming an unsafe trading instruction.  
**Primary targets:** `src/financial_brain/patterns/risk.py; risk/*; decisions/risk_filters.py`

- [ ] **CP-0205** Integrate existing deterministic `risk_gate`, portfolio limits and kill switches through a paper-only adapter.  
  **Done when:** No risk checks reimplemented inconsistently.
- [ ] **CP-0206** Define no-trade reasons: stale bars, ambiguous geometry, low liquidity, insufficient evidence, missing calibration.  
  **Done when:** Every rejected candidate shows a machine-readable code.
- [ ] **CP-0207** Restrict positive exposure recommendations to permitted paper segments and authorizations.  
  **Done when:** Short ideas never become invalid spot-equity tickets.
- [ ] **CP-0208** Display historical volatility, gap risk and max observed adverse excursions separately from stops.  
  **Done when:** No guarantee that a stop caps losses.
- [ ] **CP-0209** Add open-gap, halt, gap-through-stop and missing-quote stress cases.  
  **Done when:** Risk estimate and possible loss exceed nominal stop when appropriate.
- [ ] **CP-0210** Add trailing liquidity and ADV participation caps based on point-in-time data.  
  **Done when:** Illiquid setups are excluded before opportunity ranking.
- [ ] **CP-0211** Add family-level correlated-pattern exposure cap across the same symbol.  
  **Done when:** 5 overlapping bullish shapes do not multiply the intended position.
- [ ] **CP-0212** Stress model with bear market, shock, market-wide suspension and spread blowout synthetic scenarios.  
  **Done when:** Risk engine degrades to cash or no-trade under predeclared failures.
- [ ] **CP-0213** Implement position sizing using volatility/ATR and portfolio exposure, not historical pattern success rates.  
  **Done when:** Unknown edge estimates never cause larger allocations.
- [ ] **CP-0214** Add risk-adjusted utility as separate output from economic net alpha.  
  **Done when:** Display never subtracts risk penalty while calling value executable profit.
- [ ] **CP-0215** Add concentration-by-sector and beta diagnostics for filtered candidate baskets.  
  **Done when:** Reports attribute pattern filters' hidden factor risk.
- [ ] **CP-0216** Ensure kill switch stops new paper entries while allowing transparent lifecycle of existing positions.  
  **Done when:** Audit trail records rejection and post-kill state.
- [ ] **CP-0217** Expose risk-gate explanations for each displayed candidate and its hypothetical ticket.  
  **Done when:** Trader sees rejected rules and configured limits.
- [ ] **CP-0218** Require paper-only mode in all chart-pattern public CLI flags and integrations.  
  **Done when:** Direct live-order path fails static and runtime checks.
- [ ] **CP-0219** Create regressions that patterns cannot mutate broker credentials, approval state or OMS journal.  
  **Done when:** Test suite enforces architecture boundary.

### 14 — Signal quality, ranking and uncertainty  
**Priority:** P1 · **Purpose:** Build a useful idea list without manufacturing confidence or promising profits.  
**Primary targets:** `src/financial_brain/patterns/{rank,quality,uncertainty}.py; decisions/opportunity_card.py`

- [ ] **CP-0220** Separate geometry fit score, data quality, research evidence and forecast calibration.  
  **Done when:** UI renders four distinct concepts, not one opaque confidence number.
- [ ] **CP-0221** Design deterministic observational ranking by recency, completion, geometry and liquidity.  
  **Done when:** Ranking never claims expected return without supported data.
- [ ] **CP-0222** Deduplicate multiple patterns on the same symbol into a coherent opportunity summary.  
  **Done when:** Trader sees overlapping evidence with provenance.
- [ ] **CP-0223** Add missing-data penalty and explicit unranked status for impossible comparisons.  
  **Done when:** No stale or incomplete event outranks a valid event by default.
- [ ] **CP-0224** Expose expected holding period and event expiration based on frozen spec.  
  **Done when:** Old breakouts expire instead of appearing forever.
- [ ] **CP-0225** Implement target/invalidation zones as formula-derived reference levels, never guaranteed projections.  
  **Done when:** Chart and ticket label levels descriptive.
- [ ] **CP-0226** Add confidence intervals and calibration charts only when valid historical labels exist.  
  **Done when:** Uncalibrated UI shows `not calibrated`.
- [ ] **CP-0227** Add pattern similarity group clusters to avoid counting correlated formations as independent votes.  
  **Done when:** Summaries identify shared underlying geometry.
- [ ] **CP-0228** Create pattern-versus-momentum contrast view with baseline signal values.  
  **Done when:** Trader knows whether the pattern adds genuinely distinct information.
- [ ] **CP-0229** Produce ranked reason codes for included, rejected and abstained opportunities.  
  **Done when:** Each rank is reconstructible from stored inputs.
- [ ] **CP-0230** Implement scenario summaries: continuation, throwback, failed breakout and sideways drift.  
  **Done when:** Scenarios are descriptive with no fabricated probabilities.
- [ ] **CP-0231** Add net-cost gating for prospective paper decisions using existing cost model.  
  **Done when:** Unsupported edge always defaults to neutral/watch rather than buy.
- [ ] **CP-0232** Support user-selectable liquidity and time-window filters in the read-only experience.  
  **Done when:** Filter results are stable and completely local.
- [ ] **CP-0233** Include analyst mode vs trader mode outputs, both with mandatory evidence badges.  
  **Done when:** Trader mode suppresses research-only trade recommendations.

### 15 — Trader workstation and exploration UX  
**Priority:** P0 · **Purpose:** Make chart-pattern discovery usable by a trader with local data and no cloud services.  
**Primary targets:** `paper/{terminal,workstation,live_view}.py; interfaces/{gateway,trader_core}.py; patterns/ui/*`

- [ ] **CP-0234** Add local `Patterns` tab or view integrated with the existing terminal interface.  
  **Done when:** User can navigate there without a second disconnected application.
- [ ] **CP-0235** Show candidate, confirmed, invalidated and expired views with counts and timestamps.  
  **Done when:** Event lifecycle is intuitive and accurate.
- [ ] **CP-0236** Display ticker, lineage, timeframe, pattern, confirmation, age, direction, evidence tier, quality and data freshness.  
  **Done when:** Every row is readable without opening a separate report.
- [ ] **CP-0237** Add symbol search, family filter, timeframe, date range, status, liquidity and evidence-tier filtering.  
  **Done when:** Filters work instantly on bundled fixture data.
- [ ] **CP-0238** Add sort by discovery time, confidence-of-fit, duration, liquidity and risk.  
  **Done when:** Sorting doesn't invent expected alpha rankings.
- [ ] **CP-0239** Create pattern detail page with geometry definition, observed bars, recognition time, invalidation and tests.  
  **Done when:** Details exactly match the canonical event record.
- [ ] **CP-0240** Add side-by-side comparison of latest pattern vs baseline momentum/52-week context.  
  **Done when:** Incremental-signal question is visible at decision time.
- [ ] **CP-0241** Add `Why not trade?` panel explaining uncertainty, failed validations and position risk.  
  **Done when:** Blocked trade reasons are prominent, not hidden tooltips.
- [ ] **CP-0242** Add paper ticket preview with entry eligibility, costs, capital required and risk gate verdict.  
  **Done when:** Preview never acts as order authorization.
- [ ] **CP-0243** Add `Add to paper watchlist` and `Simulate next session` actions only.  
  **Done when:** User interactions cannot submit broker orders.
- [ ] **CP-0244** Add historical event explorer with time slider showing what was knowable at each moment.  
  **Done when:** Viewer never draws future boundaries on past dates.
- [ ] **CP-0245** Build local status page for data freshness, failed scans, audit exceptions and paper run health.  
  **Done when:** Status reports incomplete feeds honestly.
- [ ] **CP-0246** Add daily research summary with new shapes, confirmations, failures and no-trade events.  
  **Done when:** Report includes provenance and research-only labels.
- [ ] **CP-0247** Add configurable CSV/JSON export for filtered patterns with full schema and version columns.  
  **Done when:** Exports round-trip to validator.
- [ ] **CP-0248** Add keyboard navigation, readable tables, clear colors, responsive layout and accessibility labels.  
  **Done when:** Playwright or static offline UI tests cover common workflows.
- [ ] **CP-0249** Add friendly first-run empty state and example synthetic chart seed.  
  **Done when:** App remains usable when no real market data is installed.
- [ ] **CP-0250** Add quick help for terms like throwback, pivot confirmation and ultimate-high bias.  
  **Done when:** Definitions are consistent with source notes.
- [ ] **CP-0251** Add visible paper simulation banner and distinguish simulated prices from observed quotes.  
  **Done when:** Screenshot and UI regression tests assert these labels.
- [ ] **CP-0252** Add fast local navigation from a chart-pattern event to existing opportunity, journal and risk views.  
  **Done when:** Navigation uses stable lineage IDs.

### 16 — Visual chart overlays and reproducible explanation  
**Priority:** P1 · **Purpose:** Show a trader precisely when and why a shape was detected without modifying market history.  
**Primary targets:** `src/financial_brain/patterns/{overlays,explain}.py; paper/charts.py`

- [ ] **CP-0253** Define a renderer-independent overlay schema for trendlines, pivots, zones, breakout bars and geometry.  
  **Done when:** Schema accepts only validated coordinates in available bar range.
- [ ] **CP-0254** Draw tentative lines distinctly from confirmed geometry and invalidation marks.  
  **Done when:** Viewer legend clearly explains all states.
- [ ] **CP-0255** Show `known from` timestamps and any pivot confirmation lag on chart.  
  **Done when:** Historical slider can't expose future pivots.
- [ ] **CP-0256** Show the exact signal bar and earliest eligible entry bar as separate markers.  
  **Done when:** No decision is implied at the breakout close before observation.
- [ ] **CP-0257** Draw formation boundaries exclusively using data available by the displayed time.  
  **Done when:** Snapshot test detects repainting overlays.
- [ ] **CP-0258** Add before/after comparison overlay showing revisions only as new event versions.  
  **Done when:** Earlier event display remains intact.
- [ ] **CP-0259** Show risk zones, ATR range and hypothetical costs without guaranteed target claims.  
  **Done when:** UI uses neutral labels and uncertainty disclaimers.
- [ ] **CP-0260** Provide a hover breakdown of level tolerance, pivots, duration and breakout threshold.  
  **Done when:** Explainer values equal detector evaluation trace.
- [ ] **CP-0261** Render controls: matched momentum baseline and unconfirmed lookalikes when in research mode.  
  **Done when:** Trader can visually challenge cherry-picked examples.
- [ ] **CP-0262** Provide readable charts for 20 bars and 500 bars without geometry distortion.  
  **Done when:** Axes and viewport scales preserve prices and timestamps.
- [ ] **CP-0263** Create static and browser test snapshots from local synthetic fixtures.  
  **Done when:** No screenshot fetch or hosted chart library is required.
- [ ] **CP-0264** Support high-contrast/dark/light themes and symbol names with special characters.  
  **Done when:** No HTML injection or text overflow in chart legends.
- [ ] **CP-0265** Export one portable HTML/JSON chart report with all provenance embedded.  
  **Done when:** It can open offline and reproduce explanation.

### 17 — Paper journal, feedback and simulation experience  
**Priority:** P0 · **Purpose:** Give complete inspectable end-to-end paper workflows independent of actual broker access.  
**Primary targets:** `paper/{decision_journal,ledger,daily_results,execution_audit}.py; patterns/paper_adapter.py`

- [ ] **CP-0266** Add new-paper-session wizard with capital, dates, strategy filter and explicit simulated fill policy.  
  **Done when:** User can start a fixture session without internet or API keys.
- [ ] **CP-0267** Create frozen paper-run manifest linking pattern spec, code, time window, capital, data and fill assumptions.  
  **Done when:** Any change alters run identity and blocks silent overwrite.
- [ ] **CP-0268** Implement preview of all candidate paper entries before execution.  
  **Done when:** No hidden instruments or unexpected exposure entered.
- [ ] **CP-0269** Implement simulated trading calendar with next-bar execution, skipped no-trade days and delayed fills.  
  **Done when:** P&L uses fill time rather than signal time.
- [ ] **CP-0270** Record every intent and its state: rejected, expired, unfilled, partially filled, fully filled.  
  **Done when:** Coverage report reconciles intents with fills.
- [ ] **CP-0271** Record each paper position, cash movement, modeled charges and daily equity mark.  
  **Done when:** Daily accounting identity passes in synthetic trade sequences.
- [ ] **CP-0272** Implement clear closed-position ledger separating mark-to-market, realized P&L and unrealized P&L.  
  **Done when:** Open profits cannot be double-counted.
- [ ] **CP-0273** Add loss-review reports including gap-through-stop, false breakout, stale quote and spread loss.  
  **Done when:** Review can attribute loss to an explicit category.
- [ ] **CP-0274** Compute daily, weekly and monthly summary with win/loss, turnover, drawdown, exposure and benchmark.  
  **Done when:** Statistics have defined denominator and handling of missing days.
- [ ] **CP-0275** Add 'pattern contribution' analysis by family, status, liquidity bucket and regime.  
  **Done when:** Attribution is descriptive, not proof of alpha.
- [ ] **CP-0276** Provide replay-on-demand for every paper decision using stored manifest and fixture data.  
  **Done when:** Recomputed event and ledger hashes match.
- [ ] **CP-0277** Create screenshot-safe demo mode with no credentials, real holdings or sensitive history.  
  **Done when:** Demo starts from fixtures in one command.
- [ ] **CP-0278** Add warning when synthetic scenario win rate is mistaken for real forward performance.  
  **Done when:** All reports include `synthetic|replay|forward` tier.
- [ ] **CP-0279** Add integration test from synthetic bars to detector to paper preview to journal to terminal.  
  **Done when:** Entire path runs deterministically on Linux and Windows.
- [ ] **CP-0280** Add no-signal scenario and all-loss scenario to acceptance suite.  
  **Done when:** UI stays honest, reports negative outcomes and does not auto-hide losses.
- [ ] **CP-0281** Add privacy-safe portable PDF/HTML report only if existing dependencies support it; otherwise HTML+Markdown.  
  **Done when:** Report generation runs completely offline.

### 18 — Controlled self-learning and research feedback  
**Priority:** P1 · **Purpose:** Enable automatic improvement of interpretation without turning unvalidated patterns into trading policies.  
**Primary targets:** `src/financial_brain/patterns/{labels,learning,calibration,registry}.py; evaluation/learned_model.py`

- [ ] **CP-0282** Define matured-outcome labels computed only after fixed exit/observation horizons.  
  **Done when:** Training job rejects immature labels and censored outcomes are explicit.
- [ ] **CP-0283** Create versioned feature matrix of geometry, context, market regime and liquidity with knowable-at timestamps.  
  **Done when:** No label-derived or future features enter predictors.
- [ ] **CP-0284** Split historical fixture datasets chronologically with group purging and embargo.  
  **Done when:** Overlap leakage tests fail when embargo removed.
- [ ] **CP-0285** Use the existing LightGBM learner where appropriate rather than introducing unnecessary frameworks.  
  **Done when:** Training is deterministic with fixed seed and pinned schema.
- [ ] **CP-0286** Train a descriptive pattern-completion model predicting confirmation conditional on candidate state.  
  **Done when:** Output is calibrated only on valid training folds and not marketed as strategy profits.
- [ ] **CP-0287** Train an optional meta-label model for pattern-versus-momentum incremental selection.  
  **Done when:** Benchmark comparison runs on common samples and costs.
- [ ] **CP-0288** Train a regime classifier using only lagged observable inputs, if supported by existing code.  
  **Done when:** Regime label does not derive from future returns.
- [ ] **CP-0289** Implement shadow challenger registry with immutable version ID, training data hash, feature list and evaluation summary.  
  **Done when:** Model retraining never overwrites incumbent artifacts.
- [ ] **CP-0290** Implement scheduled retrain command on newly matured local fixture data.  
  **Done when:** Refit does not require APIs and remains disabled from live broker.
- [ ] **CP-0291** Add out-of-time calibration with Brier/log loss, reliability diagram and fallback to uncalibrated mode.  
  **Done when:** Missing labels yield `NO_CALIBRATION`, not invented confidence.
- [ ] **CP-0292** Implement drift detection for input features, event frequency and calibration residuals.  
  **Done when:** Drift raises review status and can disable optional model ranking.
- [ ] **CP-0293** Prevent learned-model ranking from bypassing hard geometry, data-quality or risk gates.  
  **Done when:** Property tests show no learned score can override deterministic rejection.
- [ ] **CP-0294** Implement automatic research proposal report suggesting hypotheses and ablations without executing protected trials.  
  **Done when:** Proposal includes trial-budget gate and family-multiplicity cost.
- [ ] **CP-0295** Make model rollback atomic and reversible, with old artifacts retained.  
  **Done when:** Rollback restores identical predictions and registry metadata.
- [ ] **CP-0296** Provide a continual-learning dry-run using only synthetic matured outcomes and intentional distribution shift.  
  **Done when:** System adapts or abstains and measures improvement without profit claims.
- [ ] **CP-0297** Add overfitting guard that refuses champion promotion on in-sample results or synthetic-only wins.  
  **Done when:** Promotion requires externally verified forward evidence, not completed code tasks.

### 19 — AI agent boundaries, security and prompt injection  
**Priority:** P0 · **Purpose:** Make a dependable research assistant that cannot turn untrusted pattern text into actions.  
**Primary targets:** `src/financial_brain/agents/*; security/untrusted.py; patterns/agent_tools.py`

- [ ] **CP-0298** Create read-only agent tools to list patterns, explain evidence and retrieve detector traces.  
  **Done when:** Tool schema exposes no live execution or credential mutation methods.
- [ ] **CP-0299** Validate all agent tool inputs against strict enums, date ranges, ticker IDs and query limits.  
  **Done when:** Fuzzed invalid requests are rejected before database access.
- [ ] **CP-0300** Treat book notes, scraped metadata, chart annotations and user-supplied ticker notes as untrusted data.  
  **Done when:** Injection fixtures cannot alter system permissions or instructions.
- [ ] **CP-0301** Ensure explanations cite exactly the stored event, source note, code version and data snapshot.  
  **Done when:** Agent never invents a price or research gate status.
- [ ] **CP-0302** Add deterministic structured agent outputs with evidence tiers and rejection explanations.  
  **Done when:** Schema validation catches omissions or unsupported conclusions.
- [ ] **CP-0303** Bound the maximum number of result rows and nested tool calls per query.  
  **Done when:** Agent cannot exhaust workstation resources from pathological requests.
- [ ] **CP-0304** Separate natural-language commentary from the actual numeric and risk engine.  
  **Done when:** Agent cannot recalculate prices or override risk conclusions.
- [ ] **CP-0305** Add explicit test that the agent refuses 'guaranteed profits', 'zero losses' or unsanctioned live orders.  
  **Done when:** No performance claims without evidence or live execution authority.
- [ ] **CP-0306** Add prompt-injection tests from book text, markdown links, ticker fields and CSV headers.  
  **Done when:** Untrusted content is rendered inert.
- [ ] **CP-0307** Add redaction tests for API tokens, personal account IDs and filesystem secrets.  
  **Done when:** Exports and local reports never include hidden credentials.
- [ ] **CP-0308** Add audit logs for agent queries and tool invocations with sensitive fields removed.  
  **Done when:** Read-only audit is inspectable and size-bounded.
- [ ] **CP-0309** Add an offline deterministic rule-based explanation fallback when no LLM is available.  
  **Done when:** Core product remains fully usable without hosted model.
- [ ] **CP-0310** Add graceful errors and cache invalidation for unavailable model outputs.  
  **Done when:** Model failure cannot change paper fill state or data integrity.

### 20 — Rust optimization only when demonstrated  
**Priority:** P2 · **Purpose:** Use Rust for compute-heavy chart geometry/replay only if profiling proves a benefit.  
**Primary targets:** `rust/pattern_kernels/ (conditional); pyproject.toml; scripts/bench_patterns.py`

- [ ] **CP-0311** Profile Python batch detection and incremental replay on a fixed synthetic benchmark corpus.  
  **Done when:** Baseline time, memory and profiling hotspots stored in a reproducible report.
- [ ] **CP-0312** Identify the top two bottlenecks and quantify their share of total execution.  
  **Done when:** No Rust rewrite proceeds without a recorded bottleneck.
- [ ] **CP-0313** Define numerical and event-identity parity requirements before any native kernel.  
  **Done when:** Python remains the reference implementation.
- [ ] **CP-0314** Implement optional Rust pivot/segment scan only if significant speedup is projected.  
  **Done when:** Kernel is feature-flagged and never affects unprofiled modules.
- [ ] **CP-0315** Implement optional Rust geometry candidate enumeration if justified by profiler.  
  **Done when:** Same events and known-at timestamps as Python fixtures.
- [ ] **CP-0316** Expose Rust via existing compatible PyO3/maturin practices if packaging environment permits.  
  **Done when:** FFI API is typed, safe and version-pinned.
- [ ] **CP-0317** Keep Python fallback for platforms with missing native builds.  
  **Done when:** CLI and UI operate identically without Rust.
- [ ] **CP-0318** Reject nonfinite OHLC and invalid sequence boundaries at FFI boundary.  
  **Done when:** Malformed inputs return errors rather than panics/UB.
- [ ] **CP-0319** Run differential tests over seeded random and adversarial OHLC sequences.  
  **Done when:** Stable event IDs, times and geometry agree under documented tolerances.
- [ ] **CP-0320** Benchmark speedup, memory, cold build cost and wheel compatibility on supported platforms.  
  **Done when:** Rust enabled only if a predeclared useful speedup threshold is met.
- [ ] **CP-0321** Provide rollback toggle and log active backend in every manifest.  
  **Done when:** Production experiment can reproduce both Python and Rust paths.

### 21 — Quality gates, CI and build reproducibility  
**Priority:** P0 · **Purpose:** Ensure the AI-completed implementation is testable, reviewable and durable.  
**Primary targets:** `.github/workflows/ci.yml; pyproject.toml; tests/patterns/*`

- [ ] **CP-0322** Add pattern-specific test matrix to existing Windows/Linux CI.  
  **Done when:** CI configuration runs unit, contract, integration and causality suites; if Windows is unavailable in the local environment, its verification is recorded as pending CI, not falsely passed.
- [ ] **CP-0323** Make mypy and Ruff pass for new pattern modules.  
  **Done when:** Zero new lint/type violations and no blanket exclusions.
- [ ] **CP-0324** Enforce dependency lock and offline reproducibility of fixtures from committed sources.  
  **Done when:** CI doesn't fetch market data during tests.
- [ ] **CP-0325** Add schema validation step for catalogue, pattern specs and run manifests.  
  **Done when:** Malformed committed artifacts fail CI.
- [ ] **CP-0326** Add contract coverage threshold and separate branch coverage for causal detection/state transitions.  
  **Done when:** Tests fail if critical branches are untested.
- [ ] **CP-0327** Add performance benchmark guard with conservative tolerance for CI variance.  
  **Done when:** Major regressions are reported, not silently ignored.
- [ ] **CP-0328** Add filesystem permission tests: pattern engine cannot write outside generated/cache paths.  
  **Done when:** Fixture runner works in temporary sandbox.
- [ ] **CP-0329** Add test that protected research results and past ledgers are byte-unchanged after full suite.  
  **Done when:** Golden hashes remain stable.
- [ ] **CP-0330** Add OWASP-style local input fuzzing on query parameters and HTML escaping.  
  **Done when:** No SQL injection, XSS or arbitrary file access on fixture app.
- [ ] **CP-0331** Add deterministic build/package smoke test for Linux and Windows.  
  **Done when:** `pip install -e .` and CLI invocation succeed.
- [ ] **CP-0332** Add cleanroom no-data smoke test where `data/` doesn't exist.  
  **Done when:** Program starts and shows honest empty-state information.
- [ ] **CP-0333** Add stale fixtures and calibration artifact corruption test.  
  **Done when:** App fails closed and does not claim valid pattern signals.
- [ ] **CP-0334** Add recovery smoke test for abrupt engine interrupt at every event state.  
  **Done when:** Replays produce exactly-once event entries.
- [ ] **CP-0335** Add warnings budget: isolate existing 328 warnings and fail on new pattern-library warnings.  
  **Done when:** New changes do not increase unreviewed warnings.
- [ ] **CP-0336** Generate testing evidence appendix containing command, exit code, Python version and commit SHA.  
  **Done when:** Each wave sign-off cites captured logs instead of unchecked assertions.
- [ ] **CP-0337** Document all skipped tests with reason and time-bounded remediation.  
  **Done when:** No skip conceals failure of mandatory paper safety logic.
- [ ] **CP-0338** Add CI source-capability matrix update for every new public claim.  
  **Done when:** README can't claim feature without source path and tests.

### 22 — Offline demo datasets and reproducible acceptance  
**Priority:** P0 · **Purpose:** Guarantee product usability without owner accounts, licensed data or future market observations.  
**Primary targets:** `tests/fixtures/patterns/*; scripts/demo_patterns.py; docs/DEMO-PATTERNS.md`

- [ ] **CP-0339** Generate a deterministic multi-symbol synthetic OHLCV corpus with seeded bull, bear and sideways regimes.  
  **Done when:** Exact fixture hashes are pinned in version control.
- [ ] **CP-0340** Include one positive and two negative examples per supported pattern family.  
  **Done when:** Detector regression tests cover both discovery and rejection.
- [ ] **CP-0341** Include realistic noise, spread variation, non-trading days, split and illiquidity.  
  **Done when:** Demo isn't a collection of perfect hand-drawn winning charts.
- [ ] **CP-0342** Generate candidate formations that never confirm and confirmed breakouts that later fail.  
  **Done when:** Metrics reflect selection and outcome uncertainty.
- [ ] **CP-0343** Create profitable, losing and flat synthetic paper scenarios with known arithmetic.  
  **Done when:** The ledger matches independently computed expected values.
- [ ] **CP-0344** Create a ticker with identical company changing symbol/ISIN over time.  
  **Done when:** Lineage handling is explicitly testable.
- [ ] **CP-0345** Create intraday and weekly fixtures with asynchronous bar completion times.  
  **Done when:** Mixed-timeframe engine passes prefix invariance.
- [ ] **CP-0346** Create missing-quote, delayed-fill and gap-through-stop scenarios.  
  **Done when:** Risk and paper accounting handle adverse outcomes.
- [ ] **CP-0347** Add fixture verifier checking data schema, dates, hashes and expected event IDs.  
  **Done when:** Fixtures cannot silently drift across commits.
- [ ] **CP-0348** Add one-command `python -m financial_brain.patterns.demo --offline` launch.  
  **Done when:** Opens terminal mode and optionally local UI without broker/API.
- [ ] **CP-0349** Create snapshot tests for the daily screen, empty state, risk rejection and loss review.  
  **Done when:** Trader-facing display matches expected information.
- [ ] **CP-0350** Time a complete end-to-end local demo from install to first pattern inspection.  
  **Done when:** Objective completion is documented and repeatable on CI.
- [ ] **CP-0351** Add privacy-safe sample export and explain how synthetic examples differ from actual returns.  
  **Done when:** No demo could be mistaken for profitable historical or forward evidence.

### 23 — Product docs, workflow and developer handoff  
**Priority:** P1 · **Purpose:** Give users and coding agents complete instructions without asking owners for design decisions.  
**Primary targets:** `docs/PATTERNS-{ARCHITECTURE,USER-GUIDE,DEVELOPER-GUIDE}.md`

- [ ] **CP-0352** Write architecture diagram from bar ingestion through detector, event log, research and paper UI.  
  **Done when:** Every boundary references a concrete existing module.
- [ ] **CP-0353** Write 10-minute offline user walkthrough from launch to inspect-to-watchlist-to-paper-review.  
  **Done when:** Steps reproduce on fixtures with no secret inputs.
- [ ] **CP-0354** Write pattern glossary and distinguish confirmed/pending/failed formation.  
  **Done when:** Trader can decode every UI label.
- [ ] **CP-0355** Write a complete command reference with options, outputs, exit codes and JSON formats.  
  **Done when:** All CLI examples are tested in CI.
- [ ] **CP-0356** Write detector-authoring guide with causal-window proof requirements and fixture templates.  
  **Done when:** A new pattern cannot be added without typed spec and tests.
- [ ] **CP-0357** Write research interpretation guide explaining selection conditioning and ultimate-high bias.  
  **Done when:** UI users are warned against historical book win-rate inference.
- [ ] **CP-0358** Write paper execution methodology with fill tiers and cost components.  
  **Done when:** Trader can reproduce a paper outcome from ledger entries.
- [ ] **CP-0359** Write model training/promotion limits and honest abstention behavior.  
  **Done when:** No training code is described as self-proving profits.
- [ ] **CP-0360** Write versioning and migration policy for detector/spec/catalogue/event schemas.  
  **Done when:** Documented backward compatibility is tested.
- [ ] **CP-0361** Write operations guide for data corruption, stale quotes, crashes and cache rebuild.  
  **Done when:** Manual procedures are optional; offline regression drills are automated.
- [ ] **CP-0362** Write a concise Codex agent handoff with wave order, stop gates and no-external-interference rules.  
  **Done when:** Coding agent can proceed through the entire checklist without seeking decisions.
- [ ] **CP-0363** Write final implementation scorecard with objective criteria and unprovable market-dependent criteria.  
  **Done when:** Neither subjective 10/10 nor profit claim hides the remaining evidence gates.

### 24 — Release packaging, reliability and offline production behavior  
**Priority:** P0 · **Purpose:** Ship a stable paper-only local product, not a prototype that works once.  
**Primary targets:** `src/financial_brain/patterns/cli.py; packaging; ops tests`

- [ ] **CP-0364** Expose local CLI `patterns scan`, `patterns list`, `patterns explain`, `patterns paper-demo`, `patterns audit`.  
  **Done when:** All commands have documented help and nonzero failure codes.
- [ ] **CP-0365** Integrate with existing `fb` CLI without breaking other commands.  
  **Done when:** Legacy CLI smoke tests remain green.
- [ ] **CP-0366** Implement read-only data access and append-only pattern event storage with atomic transactions.  
  **Done when:** Crashes cannot corrupt baseline data.
- [ ] **CP-0367** Add idempotent daily scan scheduling adapter using local runner entrypoints.  
  **Done when:** Repeated run for same data/spec produces no duplicate events.
- [ ] **CP-0368** Add bounded cache with explicit manifest hash invalidation on data/spec changes.  
  **Done when:** Stale detector output never reused after an input change.
- [ ] **CP-0369** Add log rotation, structured error codes and redacted diagnostics.  
  **Done when:** Runtime failures are supportable without data leaks.
- [ ] **CP-0370** Add optional backup/restore of generated pattern events and paper ledgers with checksum verification.  
  **Done when:** Restore reproduces event and account balances.
- [ ] **CP-0371** Define maximum offline resource use for small and medium local machines.  
  **Done when:** CPU/RAM limits documented and tested on representative fixtures.
- [ ] **CP-0372** Provide graceful handling when chart rendering dependencies are absent.  
  **Done when:** CLI and JSON/HTML exports still work.
- [ ] **CP-0373** Add cross-platform UTF-8, path, timezone and filesystem tests.  
  **Done when:** Windows and Unix builds agree on event identities.
- [ ] **CP-0374** Provide local startup health check for optional DuckDB, patterns schema, cache and paper journal.  
  **Done when:** Issues are reported before scanning, without modifying protected data.
- [ ] **CP-0375** Implement a no-network assert mode at process startup and provide integration test.  
  **Done when:** All required trader workflows succeed with network disabled.
- [ ] **CP-0376** Generate versioned release artifacts (wheel, docs, fixture seed metadata).  
  **Done when:** Artifacts install, import and run from a clean environment.
- [ ] **CP-0377** Add release rollback and backward-compatible state migration test.  
  **Done when:** Previous artifact can open or safely reject newer state without data loss.

### 25 — Trader acceptance and acceptance-test automation  
**Priority:** P0 · **Purpose:** Turn the UX definition into objective end-to-end pass/fail tests.  
**Primary targets:** `tests/e2e/patterns/*; docs/audits/PATTERN-ACCEPTANCE.md`

- [ ] **CP-0378** Automate task: trader identifies new confirmed formations on a synthetic watchlist.  
  **Done when:** Fixture walkthrough completes with correct event count.
- [ ] **CP-0379** Automate task: trader distinguishes tentative vs confirmed double bottom.  
  **Done when:** UI and API label both with correct known-at timestamps.
- [ ] **CP-0380** Automate task: trader asks why a breakout is not an eligible paper trade.  
  **Done when:** Rejection reason shows exact data/evidence/risk gate.
- [ ] **CP-0381** Automate task: trader previews a next-session paper intent and capital usage.  
  **Done when:** No same-bar fill and no hidden leverage.
- [ ] **CP-0382** Automate task: trader observes a profitable and losing position in same session.  
  **Done when:** Dashboard reports separate gross, net and unrealized amounts.
- [ ] **CP-0383** Automate task: trader filters candidate list by sector, liquidity, evidence and interval.  
  **Done when:** Results and totals remain consistent.
- [ ] **CP-0384** Automate task: trader inspects source notes and historical claim caveats.  
  **Done when:** Literature source is not displayed as verified India performance.
- [ ] **CP-0385** Automate task: trader replays historical formation day by day without future overlay leakage.  
  **Done when:** At each cutoff, visible geometry equals prefix data.
- [ ] **CP-0386** Automate task: trader exports a full evidence bundle for a paper intent.  
  **Done when:** Bundle includes spec, event, fill model and ledger hashes.
- [ ] **CP-0387** Automate task: trader runs no-data mode and encounters no crash.  
  **Done when:** Empty state explains what data is absent without requesting API keys.
- [ ] **CP-0388** Automate task: trader runs from fresh local installation with no network.  
  **Done when:** Demo, scans, audit and paper viewer succeed.
- [ ] **CP-0389** Automate task: trader sends a malicious ticker or source note through the UI.  
  **Done when:** Sanitization prevents execution/injection and keeps app responsive.
- [ ] **CP-0390** Automate task: trader restores a broken paper session from local backup.  
  **Done when:** Hashes reconcile and pending intents retain their states.
- [ ] **CP-0391** Automate task: trader compares a pattern to momentum-matched baseline on synthetic data.  
  **Done when:** UI reports differences without fabricated significance.
- [ ] **CP-0392** Automate task: trader sees future-market proof gates remain blocked despite full engineering completion.  
  **Done when:** `NOT_EDGE_CONFIRMED` status is mandatory and consistent.

### 26 — Final governance and honest 10/10 engineering signoff  
**Priority:** P0 · **Purpose:** Check completeness without pretending finite code tasks can guarantee future profitability.  
**Primary targets:** `docs/audits/PATTERN-FINAL-GATES.md; scripts/pattern_signoff.py`

- [ ] **CP-0393** Run fresh inventory after implementation and reconcile 72 source patterns to implemented/research-only status.  
  **Done when:** No catalogue entry lacks a truthful capability label.
- [ ] **CP-0394** Verify all P0 tasks with links to merged code paths and passing test outputs.  
  **Done when:** No checkbox auto-marks done merely because a function exists.
- [ ] **CP-0395** Verify all P1 and P2 tasks, or record conditionally-not-applicable Rust decision with benchmark evidence.  
  **Done when:** No unaccounted scope gaps.
- [ ] **CP-0396** Verify causal and no-repaint test suite across all implemented detector families.  
  **Done when:** Full and incremental output equivalence holds.
- [ ] **CP-0397** Verify every trader task end-to-end on synthetic fixtures without credentials.  
  **Done when:** Acceptance automation passes on supported platforms.
- [ ] **CP-0398** Verify paper financial ledger identity under normal, losing, partial, gap and halted scenarios.  
  **Done when:** No phantom profits or unreconciled balances.
- [ ] **CP-0399** Verify immutable source-note claims and Q4 trials remain unchanged.  
  **Done when:** Protected SHA-256 baselines match.
- [ ] **CP-0400** Verify no pattern can be promoted to confirmed edge based only on synthetic or historical demonstration.  
  **Done when:** Failure is intentional and tested.
- [ ] **CP-0401** Verify trained model versioning, shadow evaluation, drift and rollback operate on matured fixture labels.  
  **Done when:** Learning loop is reproducible but does not auto-enable live trading.
- [ ] **CP-0402** Verify no network, no cloud API, no broker action and no owner intervention were needed for core offline demo.  
  **Done when:** Sandbox integration logs show zero external calls.
- [ ] **CP-0403** Verify no investor-facing expected return or win-rate claim lacks its evidence level.  
  **Done when:** Content audit finds no unsupported claim.
- [ ] **CP-0404** Produce dual rating: `ENGINEERING_TRADER_READY` and `PROVEN_MARKET_EDGE`.  
  **Done when:** Engineering may reach excellent; market edge remains unknown until genuine forward evidence.
- [ ] **CP-0405** Record residual non-code dependencies without declaring them completed: provider attestation, rights, genuine time, real fill observations.  
  **Done when:** Final report explicitly separates external gates.
- [ ] **CP-0406** Deliver a reproducible local release tag, changelog, local test logs and any available CI evidence references.  
  **Done when:** Reviewer can independently reproduce project behavior from repo and fixtures without hosted CI access.

---

## Stage execution plan — complete without outside assistance

| Stage | Workstreams | Output | Hard gate |
|---|---|---|---|
| **S0 Repository truth** | 00, 01, 02 | Grounded baseline, validated catalogue and canonical models | All 72 rows classified; no false implementation claims |
| **S1 Causal core** | 03, 04, 08, 09 | PIT bars, swing primitives, append-only event lifecycle | Prefix invariance, no repaint, restart parity |
| **S2 Detectors** | 05, 06, 07 | Implemented pattern family engine and fixture corpus | Causal detectors, negative controls, documented missing definitions |
| **S3 Research instruments** | 10, 11, 14 | Locked study contracts, controls, descriptive ranking | Zero protected-trial consumption; no projected alpha from books |
| **S4 Paper-only economics** | 12, 13, 17 | Cost-aware paper intent, risk and reconciled ledger | No cash/equity bugs; no live order path |
| **S5 Trader UX** | 15, 16, 22, 25 | Local offline radar, chart overlays and E2E workflows | First-run, no-data, losing-trade and no-network demos pass |
| **S6 Learning and safety** | 18, 19 | Shadow learning + auditable agent explanations | Cannot bypass human/forward evidence or risk gates |
| **S7 Engineering finish** | 20, 21, 23, 24, 26 | Release-ready tests, docs, native acceleration if justified | Signed-off reproducible build and acceptance report |

The order of individual development commits may vary, but **S0 -> S1 -> S2 -> S3/S4 -> S5 -> S6/S7** safety dependencies may not be bypassed.

## Automated sign-off file contract

Emit `docs/audits/chart-patterns-completion.json` with one row per CP item:

```json
{
  "task_id": "CP-0001",
  "status": "verified_existing | implemented | not_applicable_with_evidence | incomplete",
  "source_files": ["path/to/module.py"],
  "test_files": ["tests/test_module.py"],
  "test_command": "python -m pytest -q tests/test_module.py",
  "result": "PASS",
  "code_sha": "SHA256-or-git-commit",
  "notes": "What was verified, including any limitations"
}
```

Generate the matrix by parsing the checklist IDs and demand **zero unaccounted P0 items**. Do not mark `not_applicable_with_evidence` because a feature is difficult; only valid for genuinely conditional Rust work or optional integrations. The final report must show completed task counts, test coverage, end-to-end demo proof, performance benchmarks and all residual limitations.

## Essential end-to-end quality gates

- **GATE A — Causal:** every emitted event has a valid knowable-at timestamp; all prefix/no-repaint tests pass.
- **GATE B — Correctness:** 72 catalogue rows classified truthfully; implemented detectors have positive and negative fixture cases.
- **GATE C — Research integrity:** old trials untouched; no protected-data retest; book stats never become net expected returns; unconfirmed formations retained.
- **GATE D — Paper economics:** next-eligible-bar timing, explicit modeled costs, realistic no-fills, correct cash/equity identity, honest downside and complete audit trail.
- **GATE E — Usability:** offline trader can discover, filter, inspect, replay, preview and review simulated trades with no network or API key.
- **GATE F — Reliability/security:** cross-platform CI green, injection and restart tests pass, source and schema hashes reproducible, agent read-only.
- **GATE G — Learning safety:** only matured labels, versioned challengers, shadow comparisons; never auto-promote synthetic or unverified historical results.
- **GATE H — Release:** clean install, docs, benchmark report, local no-network smoke test, verified completion matrix.

### What the final project is allowed to claim

**Allowed:** “AI-created, offline trader-usable chart-pattern research and paper-simulation module with causal geometry detection, complete audit trail, honest cost models, trader UI and governed learning infrastructure.”  
**Not allowed without future independent evidence:** “A proven profitable chart-pattern strategy”, “zero-loss trading”, “real broker execution quality”, “externally timestamp-attested forward edge”, “compliant for commercial deployment”, or “10/10 real-money readiness”.

## Copy-paste Codex / coding-agent instruction

> Use this entire document as the execution contract for Financial-Brain. Before coding, inspect the checkout and log branch/commit state; identify merged vs unmerged PR work and reuse modules already present. Implement every code-only CP item in stage order with strict tests and documentation. Never call remote broker or data APIs, access credentials, fabricate market performance, run unapproved research trials, overwrite old decisions, change protected holdouts, or enable live trading. Use deterministic fixtures and mock boundaries when real inputs are unavailable. When a task already exists, verify it with tests and mark `verified_existing`; when a prerequisite cannot be verified, keep its dependent feature safely disabled, but complete all independent code and tests. Preserve compatibility with existing Financial-Brain modules. Provide source-linked evidence for each completed CP item in `docs/audits/chart-patterns-completion.json`. Do not treat the research books as a ready-to-trade strategy. Run the full cross-platform suite, ensure no-repaint/causal and paper accounting invariants, then deliver an offline trader-demo build and a release report. Rust is permitted only for profiled bottlenecks with differential test parity and a Python fallback. Never declare financially profitable strategies, external custody or live readiness merely because engineering tasks pass.

---

**Document end.**
