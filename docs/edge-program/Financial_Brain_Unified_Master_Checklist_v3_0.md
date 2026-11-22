# Financial-Brain — Unified Master Checklist v3.0: Five Desks + Profitability + Self-Learning RL

**Created:** 2026-11-21 · **Repository:** https://github.com/crakashp2905-hub/Financial-Brain  
**Total checklist items:** 1378 = 730 prior (FB + PA) + 648 new SL items.  
**Design goal:** self-learning autonomous **research** and risk-aware, auditable **paper** trading, with economic success measured separately.  
**Status disclaimer:** All checkboxes are task requirements, not checked implementation claims. Confirm GitHub branch/commit/permissions, protect old research, and use release gates before coding.

**Execution priority:** Begin with **Part II (Self-Learning RL v3.0)** SL-00 through SL-14 for integrity, data, simulator and simple learning; use Part I for five-desk and profitability contracts. Train deep RL only after simpler baselines and evidence controls pass. Never replace owner/market permissions with generated data.

---

# Part I — Previous 730-task unified v2.0 master (preserved verbatim)

# Financial-Brain — Unified Five-Desk + Profitability Master v2.0

**As of:** 2026-11-18  
**Canonical source:** Five-Desk master v1.4 (405 original task IDs) **plus** Profitability Acceleration v2.0 (325 new `PA-xx-yy` tasks): **730 total tasks**.  
**Rule:** No old checklist checkbox is changed. Code evidence and profitability proof are evaluated separately. Follow the new execution order in the profitability plan; do not interpret section order as a license to perform all strategy research at once.

[Repository](https://github.com/crakashp2905-hub/Financial-Brain)

---

# Part A — Existing five-desk v1.4 specification (unchanged)

# Financial-Brain — Five-Desk Quant Trading & Investment Master Checklist

**Version:** 1.4 · **Prepared:** 2026-11-15 · **Target repository:** https://github.com/crakashp2905-hub/Financial-Brain

**Update in v1.4:** Preserves all 361 v1.3 tasks and adds 44 tasks for a point-in-time, non-repainting Four-Node Fractal / 20C strategy research and Indian-market paper-trading adapter (**405 total**). Includes point-in-time forecasts, six currently NSE optionable index families, all other data-covered Nifty index forecasts as research-only, calibrated distributions, executable option selection, SL/TP, prospective paper trading and validation. Previous 2026Q4 failed trials remain unchanged.

## Product mandate and non-negotiable outcome

Build one quant trading platform with five **operating desks**: (1) equity swing, (2) equity intraday, (3) options, (4) exchange-traded funds (ETFs), (5) conventional mutual funds (MFs). A desk can publish signals with the appropriate clock, issue a complete **entry BUY/SELL + linked closing SELL/BUY-TO-COVER plan with stop-loss (SL) and take-profit (TP)**, execute into a shared **paper-only** order/accounting platform, maintain a desk-level sleeve and consolidated portfolio, and prove or falsify net economic results prospectively. **Equities support both swing and intraday; ETFs support intraday/swing/position; MFs support NAV-based allocations, not intraday executions.**

**Success is risk-adjusted net economic profit evaluated on future observations**, not five forced recommendations per day, historical headline returns, the number of models, or an elevated backtest win rate. The system must permit `NO_TRADE` and `CASH`. Local simulation is not verified live fill evidence; a positive forecast is not proof of alpha. Physical broker orders remain disabled until legal/broker/owner gates are independently met.

### Status semantics
- `[ ]` proposed/unverified work, *not* a claim that no corresponding code exists; start with the repository audit.
- **P0** = necessary for paper end-to-end correctness; **P1** = required for robust quant use; **P2** = advanced research/product.
- **IN-HOUSE** = buildable with existing code and local tests/fixtures; **EXTERNAL** = real source entitlement, broker/platform access, time elapsed, independent custody or human approval that cannot be fabricated.

## 0. Baseline and integrity freeze — P0

- [ ] **FB-0001** Record current commit SHA, tree, dependency lock, test count and CI results; create new feature branch and do not rewrite the protected trial ledger.
- [ ] **FB-0002** Inventory every strategy, feed, OMS, ledger, signal renderer and output; assign a single canonical owner per capability.
- [ ] **FB-0003** Label all pre-existing historical/replayed evaluations separately from prospective paper trading; never backfill forward evidence.
- [ ] **FB-0004** Preserve previous strategies and experiment outcomes, including the 13 failed Q4 2026 protected trials. New trade rule => new version and research authorization; don't defeat the trial budget.
- [ ] **FB-0005** Add five-desk product charter and explicit execution scope: stocks, ETFs, long options, defined-risk option spreads, conventional open-ended MF schemes.
- [ ] **FB-0006** Add centrally enforced policy: `PHYSICAL_ORDERS_ENABLED = false` for the new pipeline, regardless of API credential or UI toggle.
- [ ] **FB-0007** Resolve divergent broker paths (`forward_edge/modes.py`, `execution/oms/kite_broker.py`) with hostile integration tests showing a denied order cannot escape.
- [ ] **FB-0008** Audit exchange and AMFI data retention/licensing, permitted redistribution, quote archival, contract metadata and platform terms (**EXTERNAL review**).
- [ ] **FB-0009** Establish clean CI on Windows/Linux with pinned dependencies, lint, type checks, per-module coverage and test artifacts.
- [ ] **FB-0010** Activate GitHub branch governance and required checks (**owner action**), verify actual protection via API.
- [ ] **FB-0011** Define five-desk launch gates and independent evidence labels: FIXTURE, REPLAY, LOCAL_PAPER, ATTESTED_FORWARD, BROKER_APPROVED.
- [ ] **FB-0012** Publish an audit of each task with `{id, status, source, test, artifact_hash, blocker, commit}`; make no unsupported status claims.

**Acceptance:** baseline hashes preserved; guardrail tests green; orders fail closed; every desk has a capability/unsupported matrix.

## 1. Instrument master and asset-specific clocks — P0

- [ ] **FB-0101** Build canonical `InstrumentId` with `asset_class`, `market`, `exchange`, `symbol`, `isin`, `provider_token`, `effective_from/to`, and underlying lineage.
- [ ] **FB-0102** Add `AssetClass = EQUITY | ETF | OPTION | MUTUAL_FUND` and `Desk = SWING | INTRADAY | OPTIONS | ETF | MUTUAL_FUND` independently (ETF trades can be swing/intraday).
- [ ] **FB-0103** Enforce financial unit types: INR notional, currency, equity/ETF whole units, option lots, MF fractional units, NAV per unit, premiums per unit, basis points.
- [ ] **FB-0104** Canonical `TradingClock`: exchange session vs scheme business day vs bank settlement day vs NAV publication time; timezone Asia/Kolkata with holiday calendars.
- [ ] **FB-0105** Store source timestamp, exchange timestamp when present, receipt timestamp, available-to-strategy timestamp, published-at and ingested-at.
- [ ] **FB-0106** Add point-in-time versions for listings, tick sizes, lot sizes, contract expiry, ETF underlying index and MF scheme/plan metadata.
- [ ] **FB-0107** Add instrument state: LISTED, SUSPENDED, HALTED, DELISTED, MATURED, CLOSED_FOR_PURCHASE, CLOSED_FOR_REDEMPTION, RESTRICTED.
- [ ] **FB-0108** Preserve ticker changes, corporate actions, renamed/merged schemes and ISIN changes without silently changing historical signals.
- [ ] **FB-0109** Implement effective-dated transaction calendars and fund-specific overrides from the relevant SID/KIM/AMC notice; a universal cutoff is not sufficient.
- [ ] **FB-0110** Add semantic data lineage; reject unknown instrument identity, overlapping active identities and future-published data.

**Acceptance:** same ticker across time resolves to correct ISIN; historical ETF split and MF scheme merge preserve holdings and cost basis; time travel impossible.

## 2. ETF universe, metadata and market data — P0

- [ ] **FB-0201** Build `providers/etf_reference.py` using permitted NSE/BSE/AMC reference disclosures, exchange codes, identifiers and effective-dated listing status.
- [ ] **FB-0202** Cover equity broad-market/sector/factor, bond/debt, gold, silver, international and other listed ETFs; classify exposures without assuming same benchmark or treatment.
- [ ] **FB-0203** Map ETF → underlying benchmark (prefer total-return index), fund house, inception/listing dates, NAV and indicative NAV if available.
- [ ] **FB-0204** Import historic ETF EOD OHLCV, adjusted close and trading days through existing ingestion with source hashes and corrections.
- [ ] **FB-0205** Subscribe eligible ETFs to Kite/WebSocket or permitted quote source for observed bid, ask, depth, last trade, volume and exchange timestamps (**EXTERNAL feed**).
- [ ] **FB-0206** Implement market-session safe tick/bar aggregation for ETFs, using the same low-latency code as equities but ETF-specific gates.
- [ ] **FB-0207** Implement `iNAV` freshness, source, timestamp, quote-vs-iNAV premium/discount and refusal to compare asynchronous values.
- [ ] **FB-0208** Compute effective spread, depth, median traded value, stale-quote rate, illiquidity, volatility, trading gaps, halted periods and costs at intended size.
- [ ] **FB-0209** Ingest AMC/exchange ETF disclosed tracking difference, tracking error and total expense ratio when permissioned; keep effective dates.
- [ ] **FB-0210** Detect NAV/iNAV gaps, large premiums/discounts, stale iNAV, known international underlying market closures and multi-day stale NAVs.
- [ ] **FB-0211** Track ETF creations/redemptions only as issuer/market-maker reference context, not as retail trader's instant redemption pathway.
- [ ] **FB-0212** Handle splits, distributions, fund closure and lot/tick revisions in historical and forward paper ledgers.
- [ ] **FB-0213** Keep ETF price-return series, NAV-return series and underlying total-return benchmarks separate and clearly labelled.
- [ ] **FB-0214** Avoid imputing missing live quotes from same-day NAV or daily close; label `UNPRICED`, not `FILLED`.

**Acceptance:** liquid ETF produces executable quote-backed paper fill; illiquid ETF is blocked; stale iNAV does not manufacture arbitrage; splits and distributions preserve total return.

## 3. Conventional MF data and scheme master — P0/P1

**Existing foothold:** `src/financial_brain/providers/amfi.py`, `src/financial_brain/ingest/mfnav.py`, `fb mfnav` already ingest AMFI's current NAV file; transform this into a scheme-level time-series research and accounting system.

- [ ] **FB-0301** Add `mf_scheme_master` keyed by scheme code + plan + option + ISIN, effective-dated from AMFI, AMC and SID metadata.
- [ ] **FB-0302** Distinguish direct/regular, growth/IDCW payout/reinvestment, open-ended/closed/interval, active/passive, equity/debt/hybrid/commodity/FOF and other current scheme categories.
- [ ] **FB-0303** Map fund house, riskometer, benchmark index TRI, manager and objective; preserve actual dates of classification changes.
- [ ] **FB-0304** Extend `mfnav.py` to retain NAV revision history and source publication/ingestion timestamps; do not silently backdate corrected data.
- [ ] **FB-0305** Build compliant MF NAV history ingestion from official AMFI history exports/authorized endpoints with bounded incremental windows, retries and rate limits (**EXTERNAL data acquisition**).
- [ ] **FB-0306** Respect AMFI's historical report limit of up to 90 days per download; do not circumvent permission/technical restrictions.
- [ ] **FB-0307** Create survivorship-aware historical scheme universe: merged, wound-up, renamed, closed-for-subscription schemes and suspensions remain in old dates.
- [ ] **FB-0308** Add `as_known_on()` and NAV `published_at` availability semantics; same-day after-close NAV cannot be used for a pre-cutoff decision that same day.
- [ ] **FB-0309** Ingest effective-dated direct/regular total expense ratios, AUM, exit-load schedules, minimum initial/additional investment and exit restrictions.
- [ ] **FB-0310** Track fund benchmark TRI changes and category peers, with equal-risk and same-category comparisons; prohibit comparing small-cap funds to overnight debt funds as a performance edge.
- [ ] **FB-0311** Ingest monthly/quarterly portfolio disclosures as availability permits, plus top holdings, sectors, concentration, style, duration and credit quality where applicable.
- [ ] **FB-0312** Add debt-fund metrics: duration, yield to maturity, maturity ladder, credit rating mix, downgrade and liquidity risk; label absent fields.
- [ ] **FB-0313** Add fund-of-funds lookthrough / asset-overlap estimates with double-count prevention and coverage scores.
- [ ] **FB-0314** Record exit-load schedules as time-dependent **per purchase lot**; distinguish full and partial redemption.
- [ ] **FB-0315** Add schemes with ELSS lock-in, segregated portfolios, gating, special redemption restrictions, and closed-end windows; disallow impossible redemptions.
- [ ] **FB-0316** Treat dividends/IDCW payouts, reinvestments, splits and scheme mergers as economic flows for total return rather than apparent NAV crashes.
- [ ] **FB-0317** Do not double-deduct expense ratio from returns derived from published MF NAV (TER is reflected in NAV); disclose TER for comparison and future projections separately.
- [ ] **FB-0318** Validate raw AMFI file `NAVAll.txt` mixed-dated rows and repeated scheme codes: keep each row at its own NAV date and identify stale/unavailable schemes.

**Acceptance:** direct and regular plans have separate P&L; an extinct scheme appears in historical results; no NAV look-ahead; IDCW and reinvestments reconcile, exit loads are lot-aware.

## 4. Unified signal and research contract — P0

- [ ] **FB-0401** Create immutable typed `Signal` with `signal_id, desk_id, strategy_id/version/hash, instrument_id, side, as_of, decision_at, expires_at, evidence_tier, source_hash`.
- [ ] **FB-0402** Add decision-time features and evidence, expected distribution with uncertainty, benchmark, costs, risk/return estimates and contraindications.
- [ ] **FB-0403** Add execution intent types: `EXCHANGE_LIMIT`, `EXCHANGE_MARKETABLE_SIMULATION`, `MF_SUBSCRIBE`, `MF_REDEEM`, `MF_SWITCH`, `MF_SIP`, `NO_TRADE`.
- [ ] **FB-0404** Carry entry conditions, stop/exit/invalidation, intended holding window, tick/lot/unit precision and paper execution eligibility.
- [ ] **FB-0405** Enforce signal lifecycle `DETECTED -> ELIGIBLE/REJECTED -> APPROVED/SHADOW -> SUBMITTED -> FILLED/PARTIAL/MISSED -> EXITED/EXPIRED`.
- [ ] **FB-0406** Deduplicate repeated alerts without erasing new rule versions or materially changed prices; ensure event-time ordering.
- [ ] **FB-0407** Publish up to five DAILY swing candidates and independent ETF candidate ranks; publish intraday/options signals event-driven without a compulsory trade quota.
- [ ] **FB-0408** Publish daily/weekly MF **allocation recommendations**, not misleading `instant executions`; include cutoffs, lot-level fees, benchmark and unconfirmed NAV status.
- [ ] **FB-0409** Connect researcher/scanner, event ingestion, news and signals to read-only signal generation; never let an LLM send orders directly.
- [ ] **FB-0410** Human decisions may create a separate manual-approved paper sleeve, while an autonomous paper sleeve preserves objective original outcomes.
- [ ] **FB-0411** Store rejected signals and their counterfactual follow-up observations without reclassifying them as actual portfolio P&L.
- [ ] **FB-0412** Distinguish signal quality from execution quality and calendar availability; missing data is an explicit state.

**Acceptance:** all five desks emit one schema; invalid or contradictory fields reject before ledger mutation; replay and live signals remain distinct.

## 4A. Mandatory BUY → SELL, stop-loss and take-profit lifecycle — P0 (new in v1.1)

**This is not an optional alert enhancement.** A buy recommendation without an explicit, monitored exit policy is incomplete. A precommitted *planned sell* is different from an actual sell signal or a simulated sell fill. The system must maintain all three distinctly. For permitted intraday shorts the directions reverse (`SELL_SHORT -> BUY_TO_COVER`). A stop or target is a trigger, **not a guaranteed execution price**.

**Required exchange-trade card:** `instrument + strategy + BUY/SELL_SHORT + as-of + executable entry/zone + entry expiry + quantity + SL + TP + strategy invalidation + trailing/time exit + estimated risk/reward after costs + evidence tier + paper status`. The UI must display the corresponding **closing action** immediately and later publish the real trigger-generated `SELL_TO_CLOSE` or `BUY_TO_COVER` event, linked by stable IDs.

**Mutual fund exception:** conventional MFs should display `SUBSCRIBE/ADD -> REDUCE/REDEEM/SWITCH` with target returns and downside/portfolio review thresholds **based on published NAVs**. Because the applicable redemption NAV is determined later according to platform/scheme rules, an MF 'stop-loss' is a *redemption decision trigger*, not an intraday stop order or guaranteed redemption price.

### Detailed acceptance checklist

- [ ] **FB-0451** Add typed `EntrySignal` and `ExitSignal`, with UUIDs, explicit `opens_position_id`/`closes_position_id`, strategy/version, instrument, quote-source identity, timestamp and parent signal lineage.
- [ ] **FB-0452** Require every eligible exchange trade to publish side (`BUY`, `SELL_SHORT` where permissible), executable entry limit/zone, signal expiry, size, SL, TP, holding horizon, and conditional exit policies *before* paper entry.
- [ ] **FB-0453** Use closing-side semantics: `BUY -> SELL_TO_CLOSE`; permitted intraday `SELL_SHORT -> BUY_TO_COVER`; multi-leg option spread exits must close the right quantity in every leg.
- [ ] **FB-0454** Define `ExitPolicy` with ordered triggers `HARD_STOP`, `TAKE_PROFIT`, `TRAILING_STOP`, `SIGNAL_REVERSAL`, `TIME_STOP`, `SESSION_FLATTEN`, `RISK_KILL_SWITCH`, `MANUAL_CLOSE`, and `EXPIRY_OR_CORPORATE_ACTION`.
- [ ] **FB-0455** Store stop/target basis and reference (`underlying`, `ETF market quote`, `option executable premium`, `combined option spread value`, `published NAV policy`) so unlike units are never compared.
- [ ] **FB-0456** Define `stop_price`, `target_price`, `stop_type`, `target_type`, `time_to_live`, `expected_fill_price`, `assumed_slippage`, quote timestamp and order/cost basis. Never emit ambiguous bare numbers.
- [ ] **FB-0457** Calculate R-multiple, rupee maximum *planned* loss, gross reward:risk and estimated **net** reward:risk including spread, fees, impact and taxes where modeled. Label any forecast as uncertain.
- [ ] **FB-0458** Support configurable minimum reward:risk and projected net expectancy gates by strategy; require a scientifically defended strategy rule rather than blindly forcing 2:1 targets.
- [ ] **FB-0459** Reject nonsensical plans: long SL >= entry, long TP <= entry, short SL <= entry, short TP >= entry, zero/negative tick prices, or incompatible option expiries, unless an explicitly validated exception is versioned.
- [ ] **FB-0460** Size position using stop-distance risk budget, available cash, contract lot size, notional caps, depth and correlated exposure; mark stop-loss risk as *planned*, never a guaranteed upper bound during gaps.
- [ ] **FB-0461** Make price rounding exchange tick-aware and revalidate economics *after* rounding; enforce regulatory product support for direction/venue and disallow unsupported overnight equity shorts.
- [ ] **FB-0462** Add explicit `NO_TRADE`/`WATCH` when no viable price entry, insufficient quote quality, too much slippage, option spread or fund redemption constraint defeats the plan.
- [ ] **FB-0463** Separate a proposed exit plan from an **emitted exit event**. On entry, persist both the entry and protection intent; when a trigger becomes true, publish a new timestamped SELL/BUY-TO-COVER event.
- [ ] **FB-0464** Implement persistent trigger state machines with partial-fill-aware SL/TP quantity adjustments, no duplicate exits, no overselling and restart-safe trigger evaluation.
- [ ] **FB-0465** Implement OCO-like protection in paper: the first executed stop or target cancels/reduces the sibling target/stop; do not assume a broker-native atomic OCO.
- [ ] **FB-0466** Support configurable full exits, profit scale-outs, breakeven stop moves, trailing stops, partial TP ladders and time stops; make each a versioned rule, not an after-the-fact manual adjustment.
- [ ] **FB-0467** On price gaps through SL/TP, use feasible first available executable bid/ask rather than the trigger price; record gap/slippage and preserve losses beyond planned stop.
- [ ] **FB-0468** On bar-only historical simulation where SL and TP are both touched in one bar and event order is unknown, use conservative adverse-first assumptions or report unresolved; never choose the profitable sequence by hindsight.
- [ ] **FB-0469** On tick feeds, check source/receipt timestamps, quote age, spread, depth and circuit/halts before triggering/filling. `LTP` alone can trigger research alerts but must not fabricate executable fills.
- [ ] **FB-0470** When stop triggers during quote outage or trading halt, persist an unresolved exit intent and alert; do not mark a fictional stop-price fill or silently delete open risk.
- [ ] **FB-0471** Add a per-position ledger of `ENTRY_SIGNAL`, `ENTRY_INTENT`, `ENTRY_FILL`, `STOP/TP_ARMED`, `EXIT_TRIGGER`, `EXIT_INTENT`, `EXIT_FILL`, `POSITION_CLOSED`, each with idempotency key and original snapshot.
- [ ] **FB-0472** Ensure a paper position cannot be opened without a valid exit policy, and is never treated as closed until recorded fill or correctly modeled settlement completes.
- [ ] **FB-0473** For equity swing, generate daily BUY candidates with SL/TP from versioned risk/strategy logic; after entry re-evaluate exits throughout the holding period and on gaps across sessions.
- [ ] **FB-0474** For equity intraday, publish new BUY and permitted short SELL signals from event-driven bars/ticks; evaluate SL/TP continuously and enforce a mandatory EOD flatten with missed-exit escalation.
- [ ] **FB-0475** For ETFs, support long and any separately validated supported trade styles; set SL/TP from exchange market price, not delayed NAV/iNAV, and reject untradeable spreads/volume.
- [ ] **FB-0476** For options, emit the precise `CE/PE`, strike, expiry, token, side, lots, premium entry band, SL/TP premium or risk terms, and underlying/context invalidation; default to long premiums or defined-risk spreads.
- [ ] **FB-0477** For options, monitor bid/ask/depth, premium decay, days-to-expiry, price gaps, volatility and Greeks; simulate exit on executable bid for long options and leg-wise feasible quotes for spreads.
- [ ] **FB-0478** For option spreads, express entry, SL and TP on the **combined spread value/P&L** and separately track leg risk and partial fills; prohibit a synthetic guaranteed multi-leg exit.
- [ ] **FB-0479** Block uncovered option-writing/physical-settlement exit plans until margin, assignment/exercise, expiry and settlement modules have passed coverage and operational checks.
- [ ] **FB-0480** For conventional MFs, emit `SUBSCRIBE/ADD` and corresponding `REDUCE/REDEEM/SWITCH` action plans with target allocation, target return band, downside/drawdown review threshold and review horizon.
- [ ] **FB-0481** Treat MF SL/TP as **NAV-based decision thresholds**, never exchange conditional stop orders: capture known NAV timestamp, next valid request cutoff, payment time and uncertain allotment/redemption NAV.
- [ ] **FB-0482** For MFs, when an NAV threshold is hit, generate a dated redemption/switch **request signal**; only finalize unit/cash proceeds once the applicable NAV and settlement events are known.
- [ ] **FB-0483** Support multiple fund investment lots, load aging, SIPs, STPs, SWPs, fractional units, lock-ins, minimums and tax-aware action recommendations without inventing same-day NAV fills.
- [ ] **FB-0484** For all five desks, route strategy-generated entry and triggered exits into autonomous paper, and mirror trader-approved choices in a separate comparable account.
- [ ] **FB-0485** Allow manual `APPROVE/REJECT/WATCH` and explicit manual exits, but preserve the immutable original algorithmic entry/SL/TP and human edits as separate events with timestamps.
- [ ] **FB-0486** Stream UI alerts for `BUY_READY`, `SELL_READY`, `STOP_HIT`, `TP_HIT`, `TRAIL_MOVED`, `ENTRY_EXPIRED`, `NO_FILL`, `EXIT_UNPRICED`, `MF_NAV_PENDING` and `POSITION_CLOSED`.
- [ ] **FB-0487** Show live per-position mark-to-market, realized P&L, remaining quantity, stop/target distance, expected cost-to-exit and last quote freshness; never label stale marks as live.
- [ ] **FB-0488** Emit a machine-readable and trader-friendly paired lifecycle report: original BUY timestamp and thesis, order/fill prices, original SL/TP, all revisions, exit SELL reason/time, net P&L and R-multiple.
- [ ] **FB-0489** Provide CLI/API targets: `fb signals --with-exits`, `fb positions --protective-orders`, `fb exits --desk all`, `fb paper trades --paired`, `fb paper pnl --by-exit-reason`.
- [ ] **FB-0490** Create table-driven unit tests for long/short stop and target inequalities, tick rounding, R-multiple, fees, expired entries, option contract identity and MF NAV threshold semantics.
- [ ] **FB-0491** Create deterministic price-path integration tests for SL-only, TP-only, both touched in one bar, gap-through-SL, gap-through-TP, trailing stop, partial entry and partial exit.
- [ ] **FB-0492** Create failure tests for stale quotes, feed disconnection, halts, high slippage, no liquidity, repeated ticks, out-of-order events, ledger recovery and duplicate exit submissions.
- [ ] **FB-0493** Create property tests: no position with missing protective policy; no double-selling; exit filled quantity <= open position; cash/units/debits reconcile; no forward fills before signal.
- [ ] **FB-0494** Evaluate actual net performance by exit reason (SL, TP, trail, time, discretionary) and ablate target/stop rules against simpler exits without rewriting historical decisions.
- [ ] **FB-0495** Prospective acceptance: every automatically filled exchange paper entry has a linked precommitted SL and TP or documented approved alternate exit; each later SELL/COVER is traceable, quote-feasible, and reflected exactly once in P&L.

### Canonical machine-readable signal example (illustrative only, no live recommendation)

```json
{
  "signal_id": "SWING5-EXAMPLE-0001",
  "desk": "SWING",
  "asset_class": "EQUITY",
  "instrument_id": "NSE:EXAMPLE",
  "strategy_version": "SWING_5_V1",
  "action": "BUY",
  "entry": {"limit_inr": 1000, "valid_until": "NEXT_SESSION_10_00_IST"},
  "protection": {
    "close_action": "SELL_TO_CLOSE",
    "stop_loss_inr": 970,
    "take_profit_inr": 1060,
    "time_exit_sessions": 7,
    "trigger_types": ["STOP_LOSS", "TAKE_PROFIT", "TIME_EXIT", "STRATEGY_INVALIDATED"]
  },
  "quantity": 10,
  "gross_reward_risk_ratio": 2.0,
  "fees_and_slippage_inr": "CALCULATE_AT_SIGNAL_AND_FILL",
  "paper_state": "UNSUBMITTED",
  "is_real_market_signal": false
}
```

**Paired paper lifecycle:** `ENTRY_SIGNAL -> PRECOMMITTED_SL_AND_TP -> ENTRY_INTENT -> ENTRY_FILL -> EXIT_TRIGGER (STOP/TP/TIME/OTHER) -> EXIT_INTENT -> EXIT_FILL -> BOOKED_NET_PNL`. An open position must always remain explicitly open if an exit cannot fill. Alerts are not fills. A moving stop must record each revision, not rewrite the initial risk plan.

**Acceptance:** deterministic unit/integration/property/failure tests pass for entry, exit, partial fills, gaps, stale quotes, ETF liquidity, option contract-specific bid/ask exits, and NAV-based MF redemption requests. Every filled position has one linked original entry and auditable corresponding closing decision(s); no exit or P&L is fabricated from an SL/TP trigger alone.

## 5. ETF alpha and strategy engine — P1

- [ ] **FB-0501** Cross-sectional momentum/relative-strength ranking **among comparable ETFs** with fixed horizons and exposure controls.
- [ ] **FB-0502** Trend continuation, moving-average rules, medium-horizon mean-reversion and volatility-adjusted position sizing, each pre-registered before decisive tests.
- [ ] **FB-0503** Cross-asset rotation across equity, debt, gold/silver ETF sleeves using equal-vol, buy-and-hold, same-risk and cash controls.
- [ ] **FB-0504** ETF short-horizon signal studies only for instruments whose spread/depth make cost-adjusted intraday trading plausible.
- [ ] **FB-0505** Premium/discount-to-iNAV anomaly detector with stale-iNAV and time-of-day guards; treat quoted premium as observation, not free arbitrage.
- [ ] **FB-0506** Compare market-price ETF returns net of execution friction against NAV/underlying TRI: separate tracking effect from selection alpha.
- [ ] **FB-0507** Model ETF liquidity tier, concentration, capacity, creation-unit microstructure implications and impact at submitted size.
- [ ] **FB-0508** Disallow inverse/leveraged product assumptions if absent from actual Indian listed universe; no synthetic exposures presented as available.
- [ ] **FB-0509** Use stress tests for global-hours mismatch, commodity gaps, stale iNAV, liquidity collapse and widened spreads.
- [ ] **FB-0510** Preserve fixed champion and multiple-testing adjusted challengers for each family; include no-trade and simple equal-weight ETF baselines.
- [ ] **FB-0511** Quantify incremental performance over holding the underlying benchmark with a matching risk budget and execution regime.
- [ ] **FB-0512** Emit `ETF_SHADOW_ONLY` when cost or statistical confidence fails—even if a ranking exists.

**Acceptance:** in-sample rankings and true future paper results are distinct; iNAV anomalies do not claim profits absent executable bid/ask fills; portfolio-adjusted excess is positive only when actually measured.

## 6. MF research, allocation and investment engine — P1

- [ ] **FB-0601** Produce same-category rolling 1/3/5-year returns, volatility, drawdown, downside deviation, consistency and risk-adjusted performance.
- [ ] **FB-0602** Compare every fund with its declared benchmark TRI, a low-cost passive alternative and relevant peer-group baselines.
- [ ] **FB-0603** Calculate fee-aware direct-vs-regular plan selection, avoiding fee double-counting in historical NAV returns.
- [ ] **FB-0604** Model exposure overlap, style drift, size/sector concentration, manager effects, fund size and capacity when historical disclosures allow.
- [ ] **FB-0605** Evaluate active-vs-passive fund selection with persistence controls, survivorship-aware scheme history and multiple-hypothesis penalties.
- [ ] **FB-0606** Add investor cash-management allocation: liquid/overnight/debt funds and ETFs compared against cash, accounting for payout lag and risks.
- [ ] **FB-0607** Optimize multi-fund portfolio for return, covariance, liquidity lockup, exit loads, tax, turnover, drawdown and marginal risk contribution.
- [ ] **FB-0608** Implement disciplined lump-sum, scheduled SIP, STP, SWP and rule-driven rebalance simulation with actual date/cutoff/fund availability mechanics.
- [ ] **FB-0609** Implement timing/rebalance hypotheses as independent prospective studies, never as guaranteed `buy today's NAV` outcomes.
- [ ] **FB-0610** Rank fund suggestions with `BUY_MORE | HOLD | REDUCE | SWITCH | REDEEM | NO_ACTION`, including time horizon and after-exit-load/tax rationale.
- [ ] **FB-0611** Demonstrate value beyond same-category passive funds, direct-plan benchmark and static allocations after tax and turnover; do not call mutual funds a scalping desk.
- [ ] **FB-0612** Require fund-category-specific holding periods and liquidity constraints; no fake 5-minute MF alpha.
- [ ] **FB-0613** Include owner-controlled SIP and allocation parameters without assuming access to a live folio or external fund transaction API.
- [ ] **FB-0614** Maintain versioned strategy charters and scorecard with a cash/no-action control and actual prospective NAV allotment.

**Acceptance:** no use of future NAV; fund selection outperforms appropriate category controls only if measured after loads and costs; SIP and switch dates reconcile exactly.

## 7. Mutual fund transaction lifecycle and NAV execution — P0

**Critical difference:** An MF signal creates an *intent/order request*, **not** an immediate fill at a displayed NAV. Real applications and funds availability determine the applicable NAV. For typical non-liquid/non-overnight schemes, same-business-day closing NAV generally requires valid application and funds available by the relevant 3:00 p.m. cutoff; liquid and overnight funds have distinct rules. Always source effective-dated scheme/platform rule metadata.

- [ ] **FB-0701** Create `MFOrder` with investor account/folio (synthetic in paper), scheme/plan/option, cash amount or units, transaction type and immutable request time.
- [ ] **FB-0702** Model request acknowledgement/accepted/rejected, time-stamping at official acceptance point, and platform business-day validity.
- [ ] **FB-0703** Model purchase payment initiation time, credited-to-scheme time and funds-available-for-utilization time separately; never infer one from another.
- [ ] **FB-0704** Implement effective-dated cutoff engine for normal, liquid, overnight and special schemes; holidays, online-mode exceptions and scheme suspensions.
- [ ] **FB-0705** Determine **eligible NAV date** from accepted request + fund realization + effective cutoff; reject any attempt to choose a more favorable historical NAV after the fact.
- [ ] **FB-0706** Use `NAV_PENDING` until eligible day's NAV is actually published/observed, then allot fractional MF units with proper precision.
- [ ] **FB-0707** Implement redemption with cutoff, lot selection (FIFO default unless an actual platform rule specifies otherwise), units to redeem and deferred proceeds settlement.
- [x] **FB-0708** Book exit loads by purchase lot and holding age, stamp duties and any relevant charges by effective-dated rule.
- [ ] **FB-0709** Implement redemption cash state `REDEMPTION_PENDING -> VALUE_CONFIRMED -> PAYOUT_PENDING -> CASH_AVAILABLE`; prohibit redeploying unsettled proceeds.
- [ ] **FB-0710** Simulate switches as paired switch-out and switch-in with asynchronous settlement and no phantom same-minute capital redeployment.
- [ ] **FB-0711** Model SIP installments as repeated subscriptions with independent payment outcomes and allotment NAV; missed installments must stay missed.
- [ ] **FB-0712** Model SWP as redemptions with applicable loads and payouts; STP similarly as redemption and new purchase.
- [ ] **FB-0713** Handle minimum subscription/redemption amounts, partial redemption, fractional units, folio restrictions, scheme lock-ins and unavailable NAV days.
- [ ] **FB-0714** Model NAV-related mark-to-market only after publication; earlier performance must display last-known value with a stale flag.
- [ ] **FB-0715** Model dividend/IDCW cash payouts and reinvestments as posted ledger events with correct units and basis.
- [ ] **FB-0716** Reconcile pending application, allotment confirmation, units, external flows, cash reserve and bank payout to one canonical ledger.
- [ ] **FB-0717** Distinguish assumed simulated payment realization from independently observed payment realization; forbid fabricated certainty in reports.
- [ ] **FB-0718** Build an explicit **MF PAPER REALISM** score measuring eligible-NAV certainty, payment-time evidence, load completeness and redemption settlement confidence.

**Acceptance:** buying after cutoff does not get same-day NAV; an accepted order with delayed money never gets premature units; today's NAV cannot be read before publication; redemption proceeds cannot be reused early.

## 8. Shared exchange paper OMS: equities, ETFs, options — P0

- [ ] **FB-0801** Wire all existing exchange strategies through one versioned signal→risk→paper-intent→OMS→fill→account ledger.
- [ ] **FB-0802** Use instrument-specific quantity units: stocks/ETFs whole exchange units, options standardized contracts/lots and multiple legs.
- [ ] **FB-0803** Execute fills against observed or deliberately conservative simulated bid/ask/depth, time-of-signal and feasible participation; avoid hindsight close fills.
- [ ] **FB-0804** Distinguish order accepted, working, partial fill, cancelled, rejected, missed, timed out and unknown outcome; never synthesize missing fills.
- [ ] **FB-0805** Implement entry and exit slippage, fees, taxes when applicable, impact, spreads, tick rounding, circuit/halts and exchange session close.
- [ ] **FB-0806** Maintain quote-provenance and simulation-fidelity tags (`OBSERVED_QUOTE_SIM`, `BAR_PROXY_SIM`) on every fill and performance measure.
- [ ] **FB-0807** Implement option-chain contract routing, expiry/strike/lot versioning, bid/ask and premium sizing before eligibility.
- [ ] **FB-0808** Permit long calls/puts and defined-risk debit spreads first; keep naked writing, margin-intensive strategies, unmodelled physical settlement blocked.
- [ ] **FB-0809** Implement full option leg state, partial leg fills, hedge risk, fee/margin reserve, expiry and exercise/settlement; otherwise never claim multi-leg production parity.
- [ ] **FB-0810** Implement transactionally durable outbox and post-crash idempotent reconciliation, one execution per signal/child order, no duplicate P&L.
- [ ] **FB-0811** Ensure expired or invalidated signal never fills on delayed quote; stale live feed forces `HALTED` for affected desk.
- [ ] **FB-0812** Maintain same OMS API for automatic and trader-approved paper accounts without mixing their holdings or cash.
- [ ] **FB-0813** Verify every order/receipt against instrument version, timestamps, available cash and risk limits before commit.
- [ ] **FB-0814** Rehearse exchange and WebSocket disconnects, holiday/failure sessions, partial fills, late order acknowledgements and crash recovery.

**Acceptance:** exchange paper fills are mathematically correct and auditable; no duplicate fills; fees and holdings reconcile; one desk failure doesn't corrupt another.

## 9. Shared multi-asset accounting, P&L, taxes and capital — P0

- [ ] **FB-0901** Extend `kernel/paper_account.py` into a canonical **multi-asset, multi-book, double-entry economic journal**, not five unrelated trade files.
- [ ] **FB-0902** Track real economic position IDs by instrument and funding account; independent desk sleeves are attribution views over canonical allocations.
- [ ] **FB-0903** Represent booked, available, reserved, unsettled, margin-used and pending MF payment/redemption cash separately.
- [ ] **FB-0904** Compute equity/ETF P&L from actual simulated fills + dividends/distributions + live conservative marks.
- [ ] **FB-0905** Compute option P&L from exact contract-level premium, fills, exit value and settlement/margin/fees; distinguish unrealized from realized.
- [ ] **FB-0906** Compute MF units × last published NAV + distribution income - load/transaction costs with pending-NAV and pending-cash states.
- [ ] **FB-0907** Track cash flows, contributions and withdrawals separately from returns; calculate valid time-weighted NAV returns and money-weighted returns when appropriate.
- [ ] **FB-0908** Build effective-dated India tax classification engine (equity-oriented, debt/specified funds, gold/international, scheme history) rather than applying one flat tax to all funds.
- [ ] **FB-0909** Track realized capital-gain lots, acquisition dates, tax basis, FIFO rules, holding period, estimated tax and report year; tax estimates separately from trading fees.
- [ ] **FB-0910** Prevent TER double count: **published MF NAV and ETF NAV already incorporate ongoing expense drag**; ETF exchange spread/transaction charges remain additional.
- [ ] **FB-0911** Handle stock and ETF settlement regimes; use effective-dated exchange settlement rules and block impossible early cash reuse.
- [ ] **FB-0912** Attribute P&L by desk, strategy, signal version, sector, underlying economic exposure, asset class and account, without double counting shared positions.
- [ ] **FB-0913** Calculate portfolio exposure, correlation, market beta, liquidity-adjusted VaR/ES diagnostics, overnight/gap risk and cross-desk worst-case loss.
- [ ] **FB-0914** Recognize hidden exposure overlap (NIFTY equities, NIFTY ETFs, NIFTY index funds and NIFTY options): sum underlying risks rather than treating them as diversified.
- [ ] **FB-0915** Enforce portfolio risk budgets, drawdown caps, per-instrument limits, sector/underlying limits, option premium/margin limits and MF liquidity constraints.
- [ ] **FB-0916** Mark unavailable prices and stale NAVs as uncertain/unpriced; never silently treat positions as zero or as confidently executable.
- [ ] **FB-0917** Reconcile journal→position quantities→cash/margin→NAV every day, and after every recovery.
- [ ] **FB-0918** Provide consolidated NAV and standalone desk-level NAV/returns using actual capital allocations; never average five return series without capital weights.

**Acceptance:** debit=credit for every economic event; exact replay after restart; consolidated NAV equals cash + marked assets − liabilities + pending items on a defined accounting basis.

## 10. Daily, real-time and periodic signal scheduling — P0

- [ ] **FB-1001** Pre-open: validate account, data entitlements, session calendar, tick feed, quote quality and dashboard readiness.
- [ ] **FB-1002** During open: process equity/ETF ticks and completed bars; generate event-driven intraday signals if eligible.
- [ ] **FB-1003** During open: stream underlying/options observations; signal only from actually available and sufficiently fresh contract data.
- [ ] **FB-1004** Intraday: auto-paper execute eligible orders, continuously monitor **SL/TP/trailing/time signals**, emit corresponding SELL/COVER events, and enforce end-of-day closeout where rules require it.
- [ ] **FB-1005** After market: compute up to five equity swing candidates and independently ranked ETF opportunities, locked for next eligible session.
- [ ] **FB-1006** After AMFI NAV publication: update MF NAV history, mark holdings, then produce next-business-day allocation candidates.
- [ ] **FB-1007** Before MF cutoffs: process previously approved MF subscriptions/redemptions according to scheme and payment rules; no retroactive NAV access.
- [ ] **FB-1008** Daily: close/reconcile all open/expired/missed paper intents, compute realized and unrealized P&L, audit freshness and issue verdict.
- [ ] **FB-1009** Periodic: generate weekly MF reviews, portfolio rebalance suggestions, SIP plans and liquidity-reallocation candidates.
- [ ] **FB-1010** On feed failure, data-quality error, unexpected portfolio state or high drawdown: fail closed and alert; never trade stale data.
- [ ] **FB-1011** Provide unambiguous run statuses and retry behavior; avoid double-run double-counting and orphaned pending transactions.
- [ ] **FB-1012** Measure signal latency, quote-to-fill delay, drop rates, outages and trading downtime separately for each desk.

**Acceptance:** deterministic trading-day lifecycle from data→signal→paper order→positions→NAV; non-business days do not fabricate outcomes.

## 11. Trader dashboard and workflows — P1

- [ ] **FB-1101** Single dashboard shows consolidated account NAV, available/blocked capital, daily and cumulative realized/unrealized P&L and drawdown.
- [ ] **FB-1102** Five desk pages: Equity Swing / Intraday / Options / ETF / MF; show local times and correct frequency (MF not 'live tick').
- [ ] **FB-1103** Display each signal with timestamp, provenance, intent type, candidate-vs-trade-eligible status, **BUY/SELL closing side, SL, TP, trailing/time exit and trigger state**, costs, uncertainty, risk, capacity, strategy and evidence class.
- [ ] **FB-1104** Provide `APPROVE`, `REJECT`, `WATCH`, `NO_TRADE` for manual-approval paper sleeves; no UI action directly authorizes physical cash orders.
- [ ] **FB-1105** Independent automatic paper and human-approved paper performance, with same quote feed, risk parameters and market conditions.
- [ ] **FB-1106** ETFs display underlying benchmark, bid/ask, quoted premium/discount to fresh iNAV, estimated tracking difference, cost and liquidity.
- [ ] **FB-1107** MFs display scheme/plan/option, current/published NAV, available NAV date, cutoff, pending allotment, units, cost basis, exit load, settlement and tax estimate.
- [ ] **FB-1108** Show MF `SUBSCRIBE`, `REDEEM`, `SWITCH`, `SIP`, `HOLD` as decisions, not misleading buy-at-current-price buttons.
- [ ] **FB-1109** Create trade attribution: thesis→BUY/entry signal→entry fill→precommitted SL/TP→actual SELL/exit signal→exit fill→net P&L, plus skipped/rejected opportunities.
- [ ] **FB-1110** Add alerts for order execution, stale data, MF payout/units pending, ETF dislocation, options expiry and risk budget changes.
- [ ] **FB-1111** Export reproducible CSV/Parquet/JSON + signed/hash-stamped audit reports with exact code/data revisions.
- [ ] **FB-1112** Daily owner-readable report: which desks won/lost, why, costs, cash drag, risks and whether results beat their matched benchmark.

**Acceptance:** every displayed P&L can be traced to ledger events; no recommendation is shown as executed or verified until it is.

## 12. Evaluation, performance benchmarks, research discipline — P0/P1

- [ ] **FB-1201** Freeze each algorithm with experiment ID, preregistered start, horizon, universe, costs, null, split, expected cash and acceptance gate.
- [ ] **FB-1202** Train/select/validate/forward boundaries enforced; no hindsight parameter tuning on protected forward sessions.
- [ ] **FB-1203** Evaluate stocks and ETFs against appropriate broad-market/sector/underlying TRI and same-risk cash/naive momentum baselines.
- [ ] **FB-1204** Evaluate ETFs on net executable exchange prices, not only NAV momentum or benchmark returns.
- [ ] **FB-1205** Evaluate mutual funds against matching TRI, peers, direct-plan passive alternative and scheduled no-change allocation.
- [ ] **FB-1206** Account for pending redemption, uninvested cash, actual NAV-allotment date and withdrawal delays in MF return measurement.
- [ ] **FB-1207** Calculate per-desk expectancy, hit rate, payoff ratio, profit factor, turnover, implementation shortfall, drawdown, VaR/ES diagnostic, Sharpe and alpha with confidence intervals.
- [ ] **FB-1208** Compute portfolio-level money-weighted/flow-adjusted and time-weighted performance, and incremental alpha contribution of each desk.
- [ ] **FB-1209** Robustness to costs 1×/1.5×/2×, volatility and liquidity regimes, latency, opening/closing gaps, strike changes and mutual fund payment delays.
- [ ] **FB-1210** Correct for overlapping holding periods, serial correlation, multiple testing, correlated signals and common underlying exposures.
- [ ] **FB-1211** Maintain signal-intent-denominator and fill rate; report no-fill/unknown as a coverage problem rather than excluding losers.
- [ ] **FB-1212** Compare autonomous paper versus trader-approved paper with paired eligible signal sets and explicit attribution for human intervention.
- [ ] **FB-1213** Require enough independent future sessions, non-overlapping cohorts where appropriate, verified timestamps and robust positive economic alpha before any promotion.
- [ ] **FB-1214** Publish negative outcomes with the same prominence as positive outcomes; show drawdown, lost capital and underperforming desks.
- [ ] **FB-1215** Prevent research spend/trial budget overruns and mandatory rule-version rotation; respect locked Q4 2026 ledger as-is.
- [ ] **FB-1216** Build capital-allocation committee: a desk with negative marginal risk-adjusted net performance can be allocated zero, regardless of signal count.

**Acceptance:** a positive dashboard color is not a strategy verdict; every promotion includes source hashes, data quality, trade sample, uncertainty and after-cost benchmarking.

## 13. Adversarial, property-based and acceptance tests — P0

- [ ] **FB-1301** Test all schema types, asset routing, instrument identity, timezones, calendars and decimal precision across 1000+ randomized event sequences.
- [ ] **FB-1302** Test daily equity swing 0–5 candidates; no forced trades when expected net does not cover costs.
- [ ] **FB-1303** Test 20-second intraday polling mode vs event streaming; stale/delayed quotes must not manufacture fill at a better price.
- [ ] **FB-1304** Test options zero/bad bids, nonfillable visible depth, expired tokens, wrong strike/lot, partial multi-leg fills and delayed exit.
- [ ] **FB-1305** Test ETF wide-spread and stale iNAV; no instant market-price=iNAV fill assumption.
- [ ] **FB-1306** Test ETF NIFTY/currency/commodity exposure lookthrough and double-counting with underlying stocks and derivative hedges.
- [ ] **FB-1307** Test ETF split/distribution/delisting/halt and stop handling; portfolio remains economically balanced.
- [ ] **FB-1308** Test MF direct vs regular same scheme as different share classes; scheme code/ISIN identity stable across versions.
- [ ] **FB-1309** Test MF after-cutoff application, before-cutoff application with late funds, on-time funds, weekends, bank holidays, scheme suspension and special liquid/overnight rules.
- [ ] **FB-1310** Test MF published NAV arriving after decision; same-day NAV not known in advance and no instant application-to-fill.
- [ ] **FB-1311** Test MF 100%-cash rejection on insufficient available funding, missing fund receipt confirmation and premature redemption cash use.
- [ ] **FB-1312** Test MF partial redemption, lot-specific exit loads, fractional unit precision, minimum transaction amounts and lock-in failures.
- [ ] **FB-1313** Test IDCW payout/reinvestment, fund merger, scheme winding-up and delayed redemption price publication.
- [ ] **FB-1314** Test MF expense accounting to ensure NAV-based returns are not reduced by TER a second time.
- [ ] **FB-1315** Test `pay cash for ETF` and `subscribe into equivalent index MF` with identical factor exposure but different execution/timing: never claim same NAV or same fill.
- [ ] **FB-1316** Test power loss at signal, approval, cash reserve, funds-receipt, NAV notice, allotment, redemption and payout; replay must be idempotent.
- [ ] **FB-1317** Test duplicate network events and corrections; one economic event can only post once to the ledger.
- [ ] **FB-1318** Test disabled real broker endpoints through every old and new OMS path, including malicious/accidental mode switches.
- [ ] **FB-1319** Test stress cases: 25% stock/ETF gap, 50% option premium loss, MF gated redemption, stale NAV, illiquid quote, simultaneous correlated exposure.
- [ ] **FB-1320** Run Linux + Windows full CI, minimum critical-module coverage, API contract tests, mutation/property tests and a timed market-hours failure rehearsal.

**Acceptance:** no invariant break; all mocked adverse cases preserve NAV/ledger equality; missing data never creates an invented profitable execution.

## 14. Deployment, staged release and external requirements — P0/P1

- [ ] **FB-1401** Build local single-process first, event bus and durable state second; prove correctness and latency before microservice split.
- [ ] **FB-1402** Provide deterministic offline fixtures for all five desks; do not need broker credentials to test logic (**IN-HOUSE**).
- [ ] **FB-1403** Validate permitted ETF market data, contract quotes and AMFI history from genuine sources (**EXTERNAL**).
- [ ] **FB-1404** Check live Kite access, quote throughput/limits and options-chain quote entitlement; do not hardcode assumptions (**EXTERNAL**).
- [ ] **FB-1405** Obtain actual MF order platform integration or reliable fund confirmation feed only if later enabling live mutual fund subscriptions (**EXTERNAL**).
- [ ] **FB-1406** Implement real broker and exchange controls only after separate account, approval, compliance, kill-switch and operational rehearsals (**EXTERNAL**).
- [ ] **FB-1407** Run independently observed paper sessions across representative regimes; cannot simulate elapsed future evidence (**EXTERNAL time**).
- [ ] **FB-1408** Add automatic artifact backups and restore verification for DuckDB journals, NAV and quote archives and model versions.
- [ ] **FB-1409** Document source quotas, rights, licensing, retention and failover; reject a feed when its intended use is not authorized.
- [ ] **FB-1410** Publish capability matrix: `IN_HOUSE_IMPLEMENTED`, `FIXTURE_TESTED`, `REAL_FEED_EVIDENCED`, `FORWARD_VALIDATED`, `LIVE_APPROVED`, with independent proof per desk.

**Acceptance:** software can run offline end-to-end without secrets; real forward trials use legitimate live feeds; no user is promised guaranteed profits or undocumented live order capability.

## 15. Exact first five deliverables — build in this order

**D1 — Instrument foundation:** canonical equity/ETF/option/MF identities, calendars and money/unit types; migrations, tests, import adapters.

**D2 — Data parity:** ETF live quote reference + daily history; AMFI NAV history + scheme metadata; PIT validators and flags; replay golden dataset.

**D3 — Signal parity:** five desks write the same immutable `Signal` and risk/eligibility decisions; every exchange entry includes BUY/SELL direction, executable entry, SL, TP and linked exit contract; mutual funds supply NAV-triggered redeem/switch policy; candidates, no-trade, human approvals and automated paper modes.

**D4 — Paper and accounting parity:** exchange OMS + MF order lifecycle; automatically emit and consume linked closing SELL/COVER events from triggered SL/TP/trailing/time policies; one consolidated cash/holdings/NAV ledger with independent sleeve attribution and gap/settlement policies.

**D5 — Observed prospective validation:** five-desk dashboard, auto-running local paper, actual permitted feed archive, daily after-cost reports, no-lookahead tests, champion/challenger gating.

### Proposed new package and CLI surface (names are *targets*, not existing commands)

```text
src/financial_brain/instruments/{master,units,clocks,corporate_actions}.py
src/financial_brain/providers/{etf_reference,etf_market,mf_metadata,mf_history}.py
src/financial_brain/ingest/{etf_market,etf_reference,mf_history,mf_metadata}.py
src/financial_brain/signals/{contracts,registry,router,lifecycle,exit_policy,exit_triggers}.py
src/financial_brain/strategies/{etf_rotation,etf_momentum,mf_selection,mf_allocation}.py
src/financial_brain/execution/{exchange_paper,mf_orders,mf_nav_engine,settlement,conditional_exits}.py
src/financial_brain/portfolio/{multi_asset_ledger,valuation,exposure_lookthrough,tax_lots}.py
src/financial_brain/terminal/{five_desks,signals,positions,performance}.py
tests/{test_etf_reference,test_etf_quotes,test_etf_fills,test_mf_history,
       test_mf_cutoffs,test_mf_lot_accounting,test_mf_allotment,
       test_multi_asset_ledger,test_five_desk_signals,test_no_lookahead,
       test_paper_failure_modes,test_broker_isolation,test_linked_entry_exit,
       test_stop_target_gaps,test_partial_exits,test_mf_redemption_triggers}.py

fb desks status
fb signals --desk all --mode local-paper
fb signals --desk etf --top 5
fb signals --with-exits
fb exits --desk all
fb paper trades --paired
fb signals --desk mf --frequency daily
fb paper run --desks swing,intraday,options,etf,mf
fb paper report --group-by desk
fb portfolio nav --as-of YYYY-MM-DD
fb mf orders --status pending
fb audit five-desk
```

### Minimum deployment invariants

1. For every event `debits == credits` and sum of allocations cannot exceed actually available (not pending) capital.
2. Every generated order has a pre-order immutable signal, evidence state and order timestamp; every fill has independently recorded source quote or explicit proxy simulation.
3. ETF fills occur only on the exchange calendar under executable bid/ask assumptions. MF subscription/redemption is **not** exchange matching and never fills at a quoted tick.
4. A fund subscription's applicable NAV depends on relevant scheme rules, request acceptance and funds realization; the NAV is not known before publication. Under unknown funding time, label `NAV_DATE_UNCERTAIN` and keep scenarios rather than asserting one fill.
5. Old NAV, stale ETF iNAV and unavailable option depth cannot create apparent P&L or evidence-grade alpha.
6. Desk scores report **invested capital, idle cash, fees, fund loads, stale valuation, benchmark and risk**; no positive expectancy is presumed.
7. Every exchange paper entry must have precommitted protective SL and TP or a versioned explicitly permitted alternative; every actual closing SELL/COVER is an independently timestamped linked event, not a replayed price-label; stop triggers are not guaranteed fills.
8. MF target/stop policy evaluates using published NAV and is followed by redemption request, delayed NAV-based unit realization and actual cash settlement—not an instantaneous price fill.
9. Physical orders are blocked unless separately approved with owner, broker, compliance and genuine risk/operational validation.

## Authoritative context sources (verify effective date for implementation)

- AMFI daily NAV and history: https://www.amfiindia.com/net-asset-value/nav-download (history download currently limited to 90 days per request).
- AMFI applicable NAV and funds-realization rules: https://www.amfiindia.com/investor/knowledge-center-info?zoneName=CutOffTimingsAndNewRuleOnApplicableNAV
- AMFI direct vs regular: https://www.amfiindia.com/investor/knowledge-center-info?zoneName=DirectPlan
- AMFI TER: https://www.amfiindia.com/ter-of-mf-schemes
- NSE ETF market data and iNAV: https://www.nseindia.com/market-data/exchange-traded-funds-etf
- NSE ETF/fund trading comparison: https://betanseapi.nseindia.com/static/products-services/etfs-other-mutual-funds-comparison
- NSE equity settlement: https://www.nseindia.com/static/products-services/equity-market-settlement-cycle
- SEBI fund categorization update 2026: https://www.sebi.gov.in/legal/circulars/feb-2026/categorization-and-rationalization-of-mutual-fund-schemes_99983.html
- SEBI MF Regulations 2026 index (check updated October 10, 2026 version): https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&glsId=1&sid=1&ssid=3

## Final exit criteria

The product is functionally integrated only when **all five desks** can generate appropriate entry signals with explicit SL/TP or NAV-based exit policies, automatically emit the corresponding real exit action upon a qualifying trigger, send eligible orders through paper trading under the correct market or NAV mechanics, maintain an internally consistent portfolio with verified accounting, and publish truthful, reproducible, forward-evidence-separated P&L and benchmark comparisons. **Evidence of profit must be measured and earned; never declared by completing the checklist alone.**


---

# v1.2 Profitability & Trading-Desk Operating Layer — 64 additional tasks

**Delta scope:** These tasks *augment* FB-0001–FB-1410 and FB-4A01–FB-4A45 rather than replacing them. The repository already contains partial components for capital allocation, market-regime analysis, trade journaling, champion/challenger testing, slippage, execution and monitoring. Where a capability is already coded, **integrate, independently test, and measure its real-session coverage** rather than reinventing it. No completion claim is implied until code, CI and prospective evidence exist. Asset-specific data/entitlements and elapsed market sessions remain external dependencies.

## 16. Executable expected value and signal admissibility — P0

- [ ] **FB-1601** Implement `net_ev(signal, fill_scenario, cost_model)` returning rupee expected net profit, loss distribution, confidence interval and data-fidelity tier; distinguish calibrated statistical estimates from uncalibrated heuristic scores. Reject NaN, unavailable/negative-liquidity and optimistic missing-cost inputs.
- [ ] **FB-1602** Compute SL/TP hit probabilities, time-exit probabilities, gap risk and partial-exit effects using pre-decision information only; reject naive `reward:risk = 2:1` as sufficient evidence for positive expected value.
- [ ] **FB-1603** Add strategy/desk minimum trade hurdles: expected *net* return > costs + uncertainty buffer, liquidity/size feasibility, eligible session, accepted instrument and available budget. Parameter changes require a new frozen cohort.
- [ ] **FB-1604** Implement the explicit `TRADE / WATCH / REJECT / CASH` decision with reason-coded outcomes (no edge, stale feed, confidence too low, marginal portfolio harm, cost, volatility, approvals, halted instrument).
- [ ] **FB-1605** Add executable signal price constraints: entry limit/zone, max spread, max gap/slippage and signal TTL. An expired signal needs a newly evaluated decision, never a late paper fill.
- [ ] **FB-1606** Produce a `trade_economics` receipt at decision time including gross scenario, brokerage/taxes/charges model version, risk-at-SL in INR, net TP, break-even and opportunity-cost comparison with cash.
- [ ] **FB-1607** Version and validate probabilistic forecasts with Brier/log loss, reliability diagrams and out-of-time calibration; explicitly label unreliable low-sample buckets as uncalibrated.
- [ ] **FB-1608** Add deterministic tests proving that attractive gross returns with insufficient net economic edge are skipped, while missing costs/quotes cannot default to a tradable outcome.

**Acceptance:** a generated candidate is not an eligible order until an immutable, quote-supported economic and risk receipt exists. SL/TP alone never proves profitability.

## 17. Quant capital allocation and cross-desk exposure — P0

- [ ] **FB-1701** Introduce a central `CapitalAllocator` that grants/denies reserved risk budgets *before* each desk places a paper order; aggregate all equity, ETF, option and MF sleeves against one cash/settlement ledger.
- [ ] **FB-1702** Integrate existing `risk/allocator.py`, `portfolio/next_capital.py`, `portfolio/optimizer.py`, `portfolio/gate.py` under a single enforceable interface; test they cannot grant conflicting spending permissions.
- [ ] **FB-1703** Compute overlapping exposure by underlying/sector/index/factor: an equity BUY, NIFTY ETF, index call and equity MF cannot be treated as four independent diversifiers.
- [ ] **FB-1704** Combine delta-equivalent exposure and nonlinear option scenarios with ETF and MF available holding lookthrough; report uncovered/unavailable lookthrough rather than assuming zero risk.
- [ ] **FB-1705** Support limits by desk, strategy, underlying, market beta, sector, premium-at-risk, margin-at-risk, daily drawdown, overnight gap loss and scenario stress.
- [ ] **FB-1706** Use capacity- and uncertainty-aware capital assignment with cash as a valid optimal weight; prohibit reallocating to a loss-making strategy solely because it has a recent short-term win streak.
- [ ] **FB-1707** Implement configurable daily loss/portfolio drawdown throttling, desk-level quarantine and a human-reset kill switch; no autonomous increase in leverage to recover losses.
- [ ] **FB-1708** Write atomic allocation/hold/release transactions; stress-test concurrent swing/intraday/options signal bursts, MF pending redemptions and market restart for double spending.

**Acceptance:** total available-and-reserved capital reconciles, cross-desk exposures are bounded, and correlated or marginal-value-negative trades are rejected before simulation.

## 18. Signal-to-fill latency, market microstructure and fidelity — P0

- [ ] **FB-1801** Publish per-signal timing telemetry: source/exchange timestamp, local receipt, feature readiness, signal decision, OMS approval, simulated fill, risk stop and exit execution.
- [ ] **FB-1802** Replace latency-unsafe default polling on the instant-signal path with an asynchronous streamed adapter where permitted, while preserving a clearly labelled slower polling/replay fallback.
- [ ] **FB-1803** Enforce data-freshness and sequence checks by asset class and market phase; gaps, stale depth, dropped packets or reconnection trigger no-entry, reconciliation and explicit evidence-status downgrade.
- [ ] **FB-1804** Create a volume/depth-constrained fill simulator with spread crossing, partial fills, limit-order nonfills, marketable price walks, order TTL and cancel/replace; validate minute-bar approximations separately.
- [ ] **FB-1805** Estimate implementation shortfall by desk, liquidity bucket, order size, time of day and volatility; report realized simulation error versus market observations and never treat replay fills as actual fills.
- [ ] **FB-1806** Enforce no-lookahead stop/TP trigger ordering inside OHLC bars: ambiguous intrabar paths must use conservative bounds or be marked ambiguous; never choose the favorable ordering automatically.
- [ ] **FB-1807** Track tail latency, signal expiration rates, missed-trade frequency, false signal duplication and market-data coverage in dashboard and CI/golden scenario reports.
- [ ] **FB-1808** Implement executable event replay from immutable source data through exact same decision contract; test live/replay parity on a captured trace and precisely label remaining differences.

**Acceptance:** each simulated profit has auditable decision-time data and valid simulated execution; latency and incomplete fill coverage are quantified rather than hidden.

## 19. Adaptive but auditable exits and risk containment — P0/P1

- [ ] **FB-1901** Implement one frozen `ExitPolicy` API for fixed SL/TP, ATR/volatility stop, trailing stop, staged profit-taking, time exits and strategy invalidation; version every policy tested.
- [ ] **FB-1902** Simulate paired stop-and-target exit intents with OCO semantics, ensuring one trigger cannot accidentally create two full exits or a reversed position.
- [ ] **FB-1903** Build `portfolio_exit_guard` to reduce exposure when aggregate desk risk, correlated losses or option Greeks breach limits, while respecting instrument execution mechanics.
- [ ] **FB-1904** Require all option exits to refer to exact active instrument tokens/contracts/legs; stop/TP on the underlying is a strategy trigger only, not a fabricated option premium fill.
- [ ] **FB-1905** Model overnight opening gaps and circuit limits for equities/ETFs, spread widening for options, and NAV-date/cash settlement delays for MF redemption triggers.
- [ ] **FB-1906** Evaluate partial fills, scale-outs, stop recalculation, option multi-leg close uncertainty and cancellation races without breaking cash/position invariants.
- [ ] **FB-1907** Benchmark exit variants prospectively against the frozen plain SL/TP baseline, with after-cost outcomes and multiple-comparison controls; do not optimize on the same forward trial being graded.
- [ ] **FB-1908** Save an independent linked closing `ExitSignal` for every executed exit, preserving actual trigger reason, trigger price, order intent, simulated fill price, fees and trade P&L.

**Acceptance:** every closed trade traces to a distinct original entry and exit trigger; no rule adjusts historical stops/targets after seeing a future return.

## 20. Market-specific advantage: options, ETFs and mutual funds — P1

- [ ] **FB-2001** Add options chain selection scoring on executable bid/ask, depth, open interest *with availability caveats*, IV, Greeks, theta decay, contract expiry, costs and premium-at-risk rather than underlying direction alone.
- [ ] **FB-2002** Build a constrained implied-volatility surface with quote quality checks, no-arbitrage diagnostics and time-consistent snapshots; never infer edge from an invalid/stale options quote.
- [ ] **FB-2003** Implement option strategy-level scenario P&L under underlying price, implied vol, time decay and spread shock, including defined-risk verticals and unfilled-leg contingency.
- [ ] **FB-2004** Add ETF executable fair-value comparison with iNAV/NAV freshness, exchange spread, trading size and tracking difference; apparent discount is not automatically accessible arbitrage.
- [ ] **FB-2005** Build ETF-vs-underlying-vs-competing-ETF routing for the same desired exposure: select the best *implementable net total return* after spread, depth, fee and tracking costs; preserve tax/account differences.
- [ ] **FB-2006** Build MF-vs-ETF-vs-direct equity exposure comparisons for long-term allocation using comparable TRI/category risk, available fund holdings, direct/regular plans, TER and applicable exit load/tax modeling.
- [ ] **FB-2007** Add MF tax-lot-aware switch/hold/redeem decisions, SIP/STP/SWP schedule evaluation, expected settlement cash-flow calendars and a downside policy that produces NAV-based redemption requests (not fictitious instantaneous SL fills).
- [ ] **FB-2008** Test option expiry, spread widening, stale ETF iNAV, ETF illiquidity, MF portfolio overlap, late funds realization and lot-level exit loads with deterministic adversarial fixtures.

**Acceptance:** each asset class produces correct *tradable* or *allocatable* recommendations and compares alternatives on the right economic clock, never conflating ETF ticks and MF NAV allotment.

## 21. Profit attribution, signal effectiveness and business scorecards — P0

- [ ] **FB-2101** Emit a daily consolidated report for each desk and strategy: opening NAV, cash flows, realized and unrealized P&L, fees, net return, closed/open positions and exposure.
- [ ] **FB-2102** Build a complete economic attribution: signal forecast error, market/sector/factor move, entry/exit timing, spread, impact, fees, financing/margin assumptions and unresolved/unpriced positions.
- [ ] **FB-2103** Distinguish cash-inflow-adjusted time-weighted returns, money-weighted/XIRR reporting, invested-capital returns and nominal rupee P&L; avoid claiming performance from contributions/SIPs.
- [ ] **FB-2104** Report per-desk profit factor, expectancy, median and tail loss, win rate, average winner/loser, drawdown, time-in-market, risk-adjusted return and matched benchmark net excess.
- [ ] **FB-2105** Produce a deterministic daily `why_profit_or_loss` report showing the top positive and negative economic drivers, with traceable observed inputs; do not let an LLM invent causes.
- [ ] **FB-2106** Compare autonomous vs trader-approved paper sleeves under the *same* decision-time quotes and fill rules; label human selection and execution effects separately.
- [ ] **FB-2107** Preserve rejected/expired/unfilled alternatives and their *counterfactual market outcomes* without crediting them as traded P&L; calculate selection regret with uncertainty.
- [ ] **FB-2108** Test total desk P&L = consolidated portfolio P&L after fee allocation, flows and rounding; reject any dashboard/report mismatch with the canonical ledger.

**Acceptance:** no profitable headline can conceal a cost loss, lost paper fill, external cash flow, high drawdown, or wrong benchmark.

## 22. Prospective strategy discovery and promotion — P0/P1

- [ ] **FB-2201** Extend the existing champion/challenger and protected trial infrastructure to require preregistered hypothesis, entry/exit rules, risk budget, target comparison, data cutoff and sample size *before* new paper cohorts are promoted.
- [ ] **FB-2202** Maintain a strategy registry with immutable semantic code/config hashes, features, cost version, active/paused/shadow status, capital permissions and independent desk ownership.
- [ ] **FB-2203** Compare each prospective strategy against cash, relevant instrument benchmark, cost-matched naive strategy and current champion; do not let momentum exposure masquerade as selection alpha.
- [ ] **FB-2204** Add dependent-return statistical methods (session-clustered and block-bootstrap uncertainty), confidence sequences where appropriate, and explicit multiple-testing adjustment across many candidates.
- [ ] **FB-2205** Calibrate forecast probabilities and expected return by market regime, asset, liquidity and confidence strata *only on prior data*, with sample-coverage reports and no adaptive use of protected test results.
- [ ] **FB-2206** Audit change of data vendor, latency class, order size, transaction cost or signal TTL as a material experiment version change; live-paper validation must be repeated when comparability breaks.
- [ ] **FB-2207** Enforce strategy retirement/degradation gates on forward drawdown, negative lower confidence bound, excessive shortfall, stale data and sample integrity; preserve full history of failed trials.
- [ ] **FB-2208** Produce evidence bundles with immutable signals, observed source timestamps, simulated execution receipts, strategy versions, portfolio NAV, trial registration, CI results and attestation state; never infer independent attestation from local hashes.

**Acceptance:** strategy scorecards say `EXPLORATORY`, `COLLECTING`, `FAILING`, or `FORWARD_VERIFIED` only according to frozen rules and authentic observations—not dashboard aesthetics.

## 23. Trader control room, alert ergonomics and fail-safe operations — P0/P1

- [ ] **FB-2301** Build one searchable five-desk `Today` screen with prioritized BUY, SELL, WATCH, NO_TRADE, APPROVAL_NEEDED and EXIT_TRIGGERED messages, market/data freshness and outstanding risk.
- [ ] **FB-2302** Generate two-level alerts: informational opportunity cards and immediate *risk/exit* notices; include as-of time, time-to-expiry, decision deadline, rule version, evidence tier, SL/TP and position impact.
- [ ] **FB-2303** Support human approve/reject/adjust within declared guardrails for the manual paper sleeve; once signal TTL expires, reprice and revalidate instead of using the old approval.
- [ ] **FB-2304** Build an explainable post-market desk report: signals issued, trades eligible, signals missed, filled/exited positions, TP/SL reasons, P&L, fees, skipped opportunities, risk breaches and next-session obligations.
- [ ] **FB-2305** Provide a live health screen with market feed uptime, lag, quote gaps, queue backlog, ledger consistency, pending MF allotment/redemption, option expiry exposure and stale marks.
- [ ] **FB-2306** Enforce idempotent alerts, signals, orders, fills and exit triggers after crash/reconnect; no duplicated order, repeated profit recognition or silent orphan position.
- [ ] **FB-2307** Add a documented operator runbook: market-open preflight, broker-feed failure, kill switch activation, stop-trigger ambiguities, EOD reconciliation, DB backup, restore rehearsal and evidence custody.
- [ ] **FB-2308** Define a daily acceptance gate where the desk cannot report `SESSION_COMPLETE` until all open orders, fills, paper positions, cash, fund units, fees and valuations reconcile or are explicitly flagged unresolved.

**Acceptance:** the trader can distinguish opportunity from action and profit from unverified estimates; restarting after market interruption neither creates orders nor erases losses.

## 24. 15-minute Nifty index direction + executable options program — P0/P1

**Business question:** every completed 15-minute interval, for every Nifty index with permitted and sufficiently fresh data, estimate the distribution of returns over the **next** 15 minutes (plus optional 30, 60 minutes and session-end), publish probability of UP / DOWN / FLAT, and decide whether a real, presently listed option contract has **positive expected net P&L** at an executable ask/bid and acceptable risk. **Forecast accuracy is not a substitute for positive option P&L.** `NO_TRADE` is a first-class output.

**Scope distinction:** the entire available Nifty index catalogue is a *forecast universe*, but only NSE-listed option underlyings with actual available contracts and quotes are an *options execution universe*. As checked in November 2026, NSE lists options on NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50 and NIFTYFPI. Other index forecasts may route to ETF, sector allocation or research only; do not invent option contracts. The instrument catalogue/weekly-monthly schedules must be effective-dated and refreshed from approved sources.

**Existing modules to integrate, not duplicate:** `forecasting/calibration.py`, `forecasting/ensemble.py`, `forecasting/walkforward.py`, `forecasting/null.py`, `forecasting/distribution.py`, `decisions/forecast_ev.py`, `evaluation/index_rules.py`, `features/intraday.py`, `ingest/kite_minute.py`, `ingest/options_quotes.py`, `paper/options_runner.py`, `paper/options_forward.py`, `forward_edge/session_runner.py`, `forward_edge/evidence_stats.py`, `kernel/options.py`. A new index-intraday adapter may be required because much of `forecasting/dataset.py` assumes daily adjusted equities.

### 24A. Index and derivative universe / data — P0

- [ ] **FB-2401** Build `index_master` for *all available Nifty indexes* keyed by canonical index identifier, effective-dated name/symbol, index family, provider token, exchange, publication clock and historical coverage. Retain discontinued and renamed series.
- [ ] **FB-2402** Maintain separate `optionable_index_master` from NSE's current listed derivative underlyings and effective-dated contract files: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50, NIFTYFPI at the initial snapshot; never assume all indices have options.
- [ ] **FB-2403** Ingest permitted 1-minute historical index prices and live index ticks; build as-of 15-minute OHLC bars; **do not require equity volume or five-level market depth on index packets**, which have a different Kite WebSocket schema.
- [ ] **FB-2404** Subscribe to current option contract ticks, bid/ask, visible quantities, OI, implied-vol inputs, expiry/strike/lot-size metadata, timestamps and source-quality flags; use the *actual tradable option's* depth for fills.
- [ ] **FB-2405** Maintain exchange-date-specific sessions, scheduled bar boundaries, holidays, auctions, expiry cutoffs, provider disconnects and availability calendars; never produce a fictional full bar during a halted/partial session. Confirm provider entitlements and retention limits (**EXTERNAL**).

### 24B. Chronological 15-minute labels, features and leakage controls — P0

- [ ] **FB-2406** For index i and a *completed* bar ending at time t, define `r(i,t,15m)=S(i,t+15m)/S(i,t)-1` (and explicit forward 30m/60m/EOD targets). Store actual model-decision and first-executable timestamps; no future-bar joins.
- [ ] **FB-2407** Define 3-class labels UP/DOWN/FLAT using training-period, index-specific price-noise thresholds; version thresholds; compute genuine option cost gates separately. Do not force a trade for a directional lean.
- [ ] **FB-2408** Build point-in-time features from prior 1m/5m/15m/60m returns, volatility, VWAP where meaningful, opening range, realized range, trend slope, overnight gap, time-of-day, session-to-expiry and market regime.
- [ ] **FB-2409** Add cross-index features: Nifty 50 vs Bank/Financial divergence, sector-relative strength, breadth/constituent changes where permissioned, contemporaneous option/futures OI, futures basis, IV skew, realized-versus-implied volatility and flow signals; ensure index constituent survivorship correctness.
- [ ] **FB-2410** Write leak tests: mutate every event after forecast cutoff and assert no features/probabilities change; exclude unreleased revised observations, future index membership, future close/settlement prices or option quotes received after the decision.

### 24C. Probabilistic forecasting models and adaptation — P0/P1

- [ ] **FB-2411** Train naive frequency, no-change, price-persistence and volatility/time-of-day baseline classifiers for each index/horizon; benchmark before deep learning.
- [ ] **FB-2412** Train versioned, low-variance tree/logistic models on completed bars; test a shared multi-index model versus six index-specific models and hierarchical sector indexes without leaking information across time.
- [ ] **FB-2413** Generate `P_UP`, `P_DOWN`, `P_FLAT`, expected return in index points/bps, and prediction interval; ensure probabilities sum to 1 and model uncertainty is exposed, not just a confidence score.
- [ ] **FB-2414** Fit probabilities with held-out chronological calibration (e.g. isotonic or temperature calibrator) by market regime and time-of-day only when sufficient samples exist; report Brier/log loss, reliability bins and forecasting coverage.
- [ ] **FB-2415** Use `forecasting/ensemble.py`, `forecasting/calibration.py` and `decisions/forecast_ev.py` where appropriate; compare richer models with simple baselines. Refit only on a declared rolling schedule with frozen versioning and rollback; no hindsight retraining in a purported forward run.

### 24D. Options conversion, entry/exit, SL/TP — P0

- [ ] **FB-2416** Map each index forecast to CE, PE, defined-risk debit-spread or `NO_TRADE` candidates from *presently listed instruments*; require fresh bid/ask, lot size, displayed depth, expiry and contract eligibility. No synthetic option-chain contracts.
- [ ] **FB-2417** Convert full projected underlying-return/IV/time paths into option-premium outcome distributions with delta, gamma, theta, vega, spreads and realistic execution costs. A `P_UP > 0.60` forecast alone must **not** generate BUY CE.
- [ ] **FB-2418** Rank option candidates by **expected executable net P&L**, downside tails, risk-adjusted expected value and portfolio marginal impact; compare to index/ETF alternatives and `NO_TRADE`.
- [ ] **FB-2419** Publish fully linked **BUY CE / BUY PE / BUY SPREAD** and **SELL/EXIT** intentions including exchange token, expiry, strike, lots, limit-entry band, option-premium SL/TP, underlying invalidation, time stop, trailing/partial exit behavior, quote TTL and maximum planned loss; a trigger does not guarantee fill.
- [ ] **FB-2420** Model expiry-specific restrictions, fast gamma/theta changes, sharp IV collapse, spread widening, large gaps, settlement and multi-leg fill risk; fail closed when margin/settlement/leg synchronization cannot be modeled accurately.

### 24E. Automatic paper routing and correlated risk — P0

- [ ] **FB-2421** At each valid forecast decision, append an immutable event with `forecast_id`, `index_id`, bar cutoff, data/source timestamps, strategy/config hash, model prediction, option quote snapshot, expected cost and paper eligibility.
- [ ] **FB-2422** Auto-route eligible signals through the **canonical paper OMS**. Capture simulated request, latency, accepted/partial/missed/rejected status, displayed quote/depth, fill quantity/price, fees, and linked position; replay must not masquerade as live forward evidence.
- [ ] **FB-2423** Monitor open paper options **between** 15-minute forecast updates using live market/option ticks for SL, TP, time exit, expiry and invalidation; signals update on a 15m clock, risk management does **not** wait 15m.
- [ ] **FB-2424** Aggregate exposure across NIFTY, BANKNIFTY, FINNIFTY, stocks and index ETFs; enforce underlying/beta/sector correlated risk limits, position caps and daily loss/drawdown kill switches. Prevent duplicated signal orders across refreshes.
- [ ] **FB-2425** Reconcile each index signal to `RESEARCH_ONLY`, `NO_TRADE`, `WATCH`, `PAPER_ELIGIBLE`, `UNFILLED`, `OPEN`, `PARTIALLY_EXITED`, `CLOSED` or `UNPRICED`; no ghost paper profits on missing option quotes or spreads.

### 24F. Backtest design and legitimate forward validation — P0

- [ ] **FB-2426** Construct global date-wise and session-wise time splits with purging/embargo over overlapping 15m, 30m, 60m labels and cross-index features; use fixed out-of-sample windows and strictly as-of quote availability.
- [ ] **FB-2427** Backtest index direction and option profitability separately. Direction: balanced accuracy, Brier/log loss, calibration by index/regime/clock. Trading: net INR, NAV, expectancy, drawdown, turnover, fill coverage, regret, missed trades and options-specific tail exposure.
- [ ] **FB-2428** Test against no-trade, class-frequency, always-up, momentum, simple volatility and cost-matched CE/PE null strategies; never use 50% directional hit rate as a universal baseline.
- [ ] **FB-2429** Evaluate dependent observations with session-level clustering or block bootstrap, index-correlation adjustment, multiple-testing correction, and separate expiry vs non-expiry days, high-vol vs low-vol, plus each underlying index.
- [ ] **FB-2430** Register this as a **new** strategy/version in existing trial governance; past registered Nifty index VWAP/timing family was `FAIL` (0/9) and minute-bar intraday family was `FAIL`. Do not rewrite these verdicts or claim forward alpha from historical replay. The 2026Q4 protected trial budget is spent; new protected tests require the owner's preregistered governance amendment or 2027Q1 eligibility (**EXTERNAL owner decision/elapsed time**).

### 24G. Live 15-minute operating system — P0/P1

- [ ] **FB-2431** Schedule one event at completion of every eligible 15m interval (e.g. bar ending 09:30 → predict 09:30–09:45 IST), using exchange-aware calendars and checks for fully observed bars; keep 30m, 60m, EOD as separately labeled horizons.
- [ ] **FB-2432** Implement WebSocket reconnect, backfill-on-gap, clock skew checks, inference timeouts, circuit breakers, stale-data sentinel, and idempotent resume; emit `UNAVAILABLE` not an invented forecast when critical inputs are absent.
- [ ] **FB-2433** Create an index heatmap/cockpit with per-index UP/DOWN/FLAT, directional probability, expected points, uncertainty, regime, model version, timestamp and last realized score, separating tradable options from research-only indices.
- [ ] **FB-2434** Generate actionable option cards displaying current contract, strike/expiry, CE/PE, price band, SL/TP, modeled risk-reward and **net** expected value, available quantity, confidence evidence and next-update expiry; show real-time paper P&L for approved/autonomous variants.
- [ ] **FB-2435** Publish end-of-day audit: forecast count by index and direction, missing sessions, prediction scores, trades considered, rejected, missed and filled, options premium gains/losses, costs, NAV change, worst trades, forecast-drift alerts and unresolved positions.

### 24H. Adversarial tests and acceptance — P0

- [ ] **FB-2436** Unit-test 15-minute bar closing, feature as-of boundaries, label computation, market open/close, partial bars, no-data, exchange holidays and index instrument-schema differences.
- [ ] **FB-2437** Property-test calibrated probabilities sum to 1, all candidate contracts exist in the effective-dated instrument master, and no paper order can refer to an unlisted/expired/zero-depth option.
- [ ] **FB-2438** Simulate a 15m bullish index forecast with options inflated by IV/theta/spread: expected after-cost option P&L negative => `NO_TRADE`; prevent direction-accuracy optimization from promoting a money-losing option strategy.
- [ ] **FB-2439** Exercise two simultaneous index forecasts with shared exposure, partial entry/exit fills, duplicate tick batches, reconnect, feed lag, exchange halt, expired quotes and overnight gap: no double trade, no early success, correct bounded capital and reconciled P&L.
- [ ] **FB-2440** Complete a real-session *paper-only* rehearsal with six derivative indices plus any research-only index subset, timestamped 15m signals, monitored exits, observed quote-backed paper fills and auditable separate forecast/portfolio scorecards. Require sufficient future independent evidence before calling this an edge; never auto-enable physical orders.

**Acceptance requirements:**

1. A completed 15-minute candle triggers at most one immutable forecast event per `(index, horizon, bar_end, model_version)`; missing data produces `UNAVAILABLE`.
2. Each of the six eligible option-index families is supported **only when actual current contracts and quote entitlements exist**; other Nifty indices remain research/ETF signals.
3. The platform reports probabilities, expected index moves, uncertainty and evidence labels; only net-positive **estimated** option trades satisfying full risk/cost/quote gates can be candidates for paper execution.
4. Paper entries require current observed option bid/ask with sufficient depth; SL/TP/SELL exits are monitored between prediction intervals; missing exits do not manufacture realized profits.
5. 15m directional skill and economic option P&L are evaluated **separately**, against meaningful nulls, after actual costs, and on new sessions not used to train the model.
6. Strategy governance respects the existing failed trials. A functioning service is **not evidence of profitability**.

#### Suggested signal contract (illustrative schema only)

```json
{
  "forecast_id": "NIFTY50-20261116-0930-15M-v1",
  "index_id": "NIFTY_50",
  "bar_end_ist": "2026-11-16T09:30:00+05:30",
  "horizon_minutes": 15,
  "prediction_interval_ist": ["2026-11-16T09:30:00+05:30", "2026-11-16T09:45:00+05:30"],
  "probabilities": {"up": null, "down": null, "flat": null},
  "expected_return_bp": null,
  "predicted_interval_bp": null,
  "evidence_tier": "UNVALIDATED",
  "tradable_underlying": true,
  "option_trade": {
    "action": "NO_TRADE",
    "instrument_token": null,
    "contract": null,
    "side": null,
    "entry_limit": null,
    "stop_loss": null,
    "take_profit": null,
    "expected_net_inr": null,
    "planned_exit": null
  },
  "paper_status": "NOT_SUBMITTED",
  "comment": "Example contract: all probabilities and trade prices intentionally null"
}
```

**Operational note:** The forecast bar frequency is not the options-execution latency. Tick-driven risk and exits run independently. Kite index packets do not include options' bid/ask depth; quote/depth must come from the *option instruments*. Tick/OI subscription and archival require permitted upstream data, not scraping or internally simulated provider rights.

**Official references (verified 2026-11-15):**
- NSE index underlyings / 2026 derivatives contract specifications: https://www.nseindia.com/products-services/equity-derivatives-list-underlyings-information ; https://www.nseindia.com/static/products-services/equity-derivatives-contract-specifications
- Kite WebSocket (distinct index packet structure and order-book depth): https://kite.trade/docs/connect/v3/websocket/
- Kite historical candles (including `15minute` interval): https://kite.trade/docs/connect/v3/historical/

## v1.3 prioritized execution sequence

**15-minute Nifty index forecast critical path:** FB-2401–FB-2410 (clean index universe/data/labels) → FB-2411–FB-2415 (probabilistic model) → FB-2416–FB-2425 (real options and paper execution) → FB-2426–FB-2430 (valid evaluations) → FB-2431–FB-2440 (live runtime and adversarial proof). These new tasks take precedence over speculative new strategy families until the signal-to-option-P&L loop works.


1. **First (P0):** FB-1601–FB-1608; FB-1701/1702/1708; FB-1801/1803/1806/1808; FB-2101/2108. These close the gap between displaying attractive signals and earning auditable simulated P&L.
2. **Next (P0):** FB-1901/1902/1905/1906/1908; FB-2305/2306/2307/2308. These make entry, exit, account and operations coherent under failure.
3. **Then (P1):** FB-2001–FB-2008 for asset-class-specific edge; FB-2201–FB-2208 for prospective proof of any putative alpha; FB-2102–FB-2107 for useful trading decisions.
4. **Only after forward data exist:** enable conditional allocation increases for independently validated strategies subject to uncertainty, capacity and portfolio risk. Never require five trades or guaranteed positive outcomes.

### New acceptance scenarios for the automation suite

| Scenario | Required outcome |
|---|---|
| A signal predicts +1% gross, all-in round trip costs exceed its reliable edge | REJECT or WATCH, no paper order |
| Attractive trade duplicates existing NIFTY risk across equity + ETF + call + index fund | Reduce or deny allocation; consolidated stress unchanged within limits |
| Both SL and TP lie inside an intraday OHLC bar with unknown path | Conservative bounded result or AMBIGUOUS, no fabricated favorable fill |
| Option model says BUY while bid/ask are stale or depth insufficient | Not executable; remain research signal |
| MF redemption trigger occurs at 11 AM but applicable NAV is published later | Pending redemption intent; allotment/settlement at actual applicable NAV when known |
| Signal TTL expires during quote feed outage | EXPIRED, no catch-up fill at an observed later favorable quote |
| Paper runner crashes after recording intent but before simulated execution | Recover exactly once; no duplicate fees, shares, cash or P&L |
| 5 signals generated, 2 rejected, 1 expired, 2 paper-filled | Signal denominator is 5; 2 trade positions only; rejected/expired cannot count as profit |
| Trader approves only the prior winners | Cannot be backdated into the prospective manual portfolio |
| Cash deposited during the period | Rupee NAV increases; trading performance does not falsely increase |

## v1.3 completion rule

A *profit-ready simulation platform* is one that can **automatically generate, assess, execute, close, reconcile and evaluate** all eligible instrument-specific paper trades while rejecting non-executable signals and preserving true costs, timing and uncertainty. It is **not equivalent to a profitable live strategy**. Only separately earned, prospective, sufficiently powered and risk-aware net economic results justify a trading-edge claim; physical execution still requires independent owner/broker/compliance gates.


## 25. Four-Node Fractal / 20C strategy — XAUUSD source and Indian-market adaptation (v1.4)
**Purpose:** Incorporate the user-provided “2025 0822 0930 One Minute - 4 Node Strategy” for XAUUSD M1 into Financial-Brain as a **new, separately versioned research hypothesis** and optional paper-only strategy source for Indian index, futures, equity, ETF and option desks. This is **not** a verified profitable strategy, and previous failed intraday trials must not be overwritten.
**Original core:** 20C = SMMA20(Low, Close, High); SMMA8/50/100/200(Close); confirmed alternating fractal N1/N2/N3; M1 candle close through N2; N1/N2/N3 vs 20C gates; D1≤15 pips; D2<10 pips; max SL 15 pips; TP 20 pips; initial SL beyond N3 plus spread; at +10 pips remove TP and ratchet SL by +5 pips at successive 5-pip favorable steps; N4 wick and significant-candle exits; at least 20-pip clearance from daily Fibonacci and MA50/100/200. All forex pips/lot sizing are **source-specific**, not Indian-market parameters.
**Unresolved source conflicts (hard blockers for automatic signals):** BUY exception says close *below* N2; BUY and SELL N3 midpoint tests may not be symmetric; N2 buy “20H High” ambiguous; N4 not formally defined; significant-candle wick rule unclear; Fibonacci anchors unspecified; “return candle” and exceptional N3≥N1 timing unspecified. Record explicit owner decisions, preserve alternative interpretations, and never silently repair them.
### 25A. Source preservation and ambiguities
- [ ] **FB-2501** Archive the user-supplied 2025-08-22 09:30 XAUUSD M1 Four-Node rules verbatim in a versioned specification with source hash and no silent edits. **Acceptance:** Immutable original specification and checksum are committed.
- [ ] **FB-2502** Create `FourNode_XAU_Original_v1` as non-executable research reference and `FourNode_India_v1` as separately versioned, initially paper-only implementation. **Acceptance:** Both have independent configuration and provenance.
- [ ] **FB-2503** Flag BUY exception text (“below N2”) as contradictory to normal BUY (“above N2”); require explicit owner-confirmed interpretation before executable signals. **Acceptance:** Ambiguous BUY variant fails closed.
- [ ] **FB-2504** Flag BUY N3 <= 20C midpoint and SELL N3 <= 20C midpoint symmetry/asymmetry as unresolved; preserve both alternative hypotheses without assuming correction. **Acceptance:** Variants have named rules and are never merged in one trial.
- [ ] **FB-2505** Flag N2 sell <= 20C Low, N2 buy <= 20C High, and original “20H High” typo; confirm intended comparator, reference bar and inclusive equality. **Acceptance:** Tests show behavior on boundary cases.
- [ ] **FB-2506** Define N4 precisely (fourth confirmed fractal pivot versus breakout candle), its confirmation time, wick test and exit trigger; block until specified. **Acceptance:** No fabricated N4 can trigger entry/exit.
- [ ] **FB-2507** Specify “significant candle” rigorously: equal upper/lower wick tolerance, both wicks > body or combined wick > body, doji handling, and when checked. **Acceptance:** Explicit fixtures distinguish competing interpretations.
- [ ] **FB-2508** Define Fibonacci anchors, timezone/session boundaries, daily reset, whether levels come from previous completed day, and levels outside 0–100%; never use current-day final high/low. **Acceptance:** Prior-day-only levels reproduce without lookahead.
### 25B. Indicators, fractals and node state machine
- [ ] **FB-2509** Implement configurable Wilder-style SMMA(20) separately for Low, Close and High as `20C`; specify initialization, warm-up, missing-bar and session reset policies. **Acceptance:** Golden OHLC fixtures reproduce all three bands.
- [ ] **FB-2510** Implement SMMA(8), SMMA(50), SMMA(100), SMMA(200) on Close with explicit warm-up and consistent alignment; do not silently replace SMMA with EMA/SMA. **Acceptance:** Numerical parity tests against independent reference calculations.
- [ ] **FB-2511** Implement the complete original daily Fibonacci ratio set: -323.4, -200, -161.8, -141.4, -100, -76.4, -61.8, -41.4, -27, -12.8, 0, 11.4, 23.6, 38.2, 50, 61.8, 76.4, 88.6, 100, 112.8, 127, 141.4, 161.8, 176.4, 200, 261.8, 423.6. **Acceptance:** Levels and anchor timestamps are versioned and tested.
- [ ] **FB-2512** Build configurable fractal detector with left/right confirmation windows, plateau/tie policy, minimum swing size and pivot-high/pivot-low alternation. **Acceptance:** Future bars do not alter previously emitted confirmed node events.
- [ ] **FB-2513** Store node `pivot_at` separately from `confirmed_at`, `observable_at`, price, side, parent chain and source bar IDs. **Acceptance:** No node is eligible before `observable_at`.
- [ ] **FB-2514** Implement N1→N2→N3→N4 zigzag state machine, reset/invalidation rules, expiry, same-bar conflict handling and idempotent event replay. **Acceptance:** Deterministic online/replay parity.
- [ ] **FB-2515** Enforce exactly three permanently confirmed trending fractal nodes before evaluating breakout through N2 on a completed M1 candle. **Acceptance:** No pivot discovered from future bars is traded retroactively.
- [ ] **FB-2516** Define buy/sell direction, N1-to-20C High/Low, N3-to-20C Mid, N2-to-20C limits and exception logic as explicit independent predicates with reason codes. **Acceptance:** Every rejected/eligible setup lists passed/failed predicates.
### 25C. Entry filters and exit engine
- [ ] **FB-2517** Implement close-confirmed N2 breakout with strictly later actionable order timestamp; choose next available quote/bar execution rather than filling at a known completed close. **Acceptance:** No signal uses same-bar hindsight fill.
- [ ] **FB-2518** Implement D1=|N2−N3| and D2=|entry−N2| with inclusive/exclusive boundaries configurable; original 15/10 pip limits are preserved only for XAU baseline. **Acceptance:** Threshold boundary fixtures pass.
- [ ] **FB-2519** Enforce minimum distance to daily Fibonacci levels and SMMA50/100/200, accounting for direction, support/resistance geometry and original 20-pip baseline. **Acceptance:** Barriers ahead and behind entry are distinguished.
- [ ] **FB-2520** Implement broker spread-aware initial stop beyond N3 (SELL above, BUY below) and reject trades with excessive initial risk. **Acceptance:** Stops obey exchange tick rounding and no invalid-side orders.
- [ ] **FB-2521** Implement original 20-pip TP and max 15-pip stop as XAU-specific configurable baseline, with separate ATR/tick/percent alternatives for Indian assets. **Acceptance:** No assumption that XAU pip == Indian tick.
- [ ] **FB-2522** Implement the original trailing rule: at +10 pips remove fixed TP and move SL to entry+5 pips for long / entry−5 pips for short; ratchet at each additional +5 pip favorable move. **Acceptance:** State-machine tests cover every threshold and never loosen stops.
- [ ] **FB-2523** Clarify whether the 10-pip activation and 5-pip SL offset are measured on bid/ask executable prices, and prevent spread-induced false trigger or impossible fills. **Acceptance:** Long/short spread tests pass.
- [ ] **FB-2524** Implement N4 wick <5 pip filter and significant-candle early exit as separate, precisely timestamped controls, pending source ambiguity resolution. **Acceptance:** Unknown N4 or candle definition returns BLOCKED.
### 25D. Indian-market adaptation and options routing
- [ ] **FB-2525** Create per-instrument conversion config for tick size, ATR normalization, spread, expected slippage, trading hours, contract multiplier and corporate action adjustment. **Acceptance:** Same rule runs on Nifty, Bank Nifty, stocks and ETFs with correct units.
- [ ] **FB-2526** Replace forex balance multiplier X with capital-at-risk sizing; retain X=1→0.01 lots per $100 only as original forex demonstration, never apply it to Indian options. **Acceptance:** Risk budget and lot constraints cannot be exceeded.
- [ ] **FB-2527** Prioritize underlying Nifty/Bank Nifty and liquid equities/ETFs for directional tests; maintain symbol-specific data-quality and tradability checks. **Acceptance:** No unsupported instrument or imaginary contract receives a trade.
- [ ] **FB-2528** Bridge underlying fractal BUY/SELL signal into index options CE/PE candidate generation without assuming an underlying move implies positive option P&L. **Acceptance:** Underlying signal and option decision are separate auditable records.
- [ ] **FB-2529** Evaluate option expiry, strike, observed bid/ask, IV, Greeks, time decay, liquidity and depth for each CE/PE candidate at signal time. **Acceptance:** Stale/unavailable option quotes cause NO_TRADE.
- [ ] **FB-2530** Map underlying invalidation and exit conditions to option-premium SL/TP, max premium risk, time stop and tick-driven exit monitoring; avoid copying index points into option premium ticks. **Acceptance:** Options paper exits use actual quote-backed premium and costs.
- [ ] **FB-2531** Implement separate futures and ETF execution adapters with correct contract sizing, margin/settlement, liquidity and fee models; paper-only by default. **Acceptance:** Cross-asset unit and ledger tests pass.
- [ ] **FB-2532** Integrate as an optional signal source into the 15-minute Nifty forecasting desk while keeping native M1 pattern clock and independent tick-driven risk clock. **Acceptance:** No 15-minute delay for a one-minute signal or protective exit.
### 25E. Research integrity, evaluation and release
- [ ] **FB-2533** Reuse `patterns/pivots.py`, `patterns/streaming.py`, `kernel/indicators.py`, `ingest/kite_minute.py`, `paper/intraday_runner.py` and `paper/options_runner.py` after interface audit; avoid duplicate frameworks. **Acceptance:** Architecture mapping documents reuse versus gaps.
- [ ] **FB-2534** Build point-in-time replay with original bar timestamps, historical instrument eligibility, warm-up and confirmed-pivot latency; disallow revised ZigZag history as trading evidence. **Acceptance:** Streaming and historical signals match when fed identical observations.
- [ ] **FB-2535** Test the unmodified XAU baseline only on suitable XAU data if legally available; label unsupported or unavailable input honestly. **Acceptance:** No false XAU validation claim.
- [ ] **FB-2536** Pre-register India variants, instrument universe, parameter bounds, entry/exit rules, cost model and statistical gates before evaluation; respect protected trial budgets. **Acceptance:** Immutable registration and trial-family accounting.
- [ ] **FB-2537** Run walk-forward tests across disjoint time windows and market regimes; purge/embargo overlapping targets and group by trading day. **Acceptance:** No time leakage; report independent sample counts.
- [ ] **FB-2538** Benchmark against simple N2 breakout, opening-range breakout, random-entry matched-exposure, and no-trade baselines. **Acceptance:** Report incremental net alpha and uncertainty.
- [ ] **FB-2539** Use realistic spread, latency, commissions, taxes, price gaps, option premium decay and conservative same-bar SL/TP ordering. **Acceptance:** All trades reconcile to quote-backed or explicitly bounded execution.
- [ ] **FB-2540** Perform parameter stability and multiple-testing corrections across fractal widths, moving-average variants, ATR scaling, Fibonacci filters and asset families. **Acceptance:** No cherry-picked winner promoted without adjustment.
- [ ] **FB-2541** Track funnel: raw pivots → confirmed patterns → qualified setups → buy/sell intents → observed paper fills → closing events → net P&L, including rejects and expirations. **Acceptance:** Every count reconciles to event IDs.
- [ ] **FB-2542** Add per-trade evidence: N1–N4 coordinates and confirmation times, 20C bands, SMMA8/50/100/200, Fibonacci barriers, D1/D2, spread, SL/TP and exit reason. **Acceptance:** Human-readable trace and machine-verifiable event record.
- [ ] **FB-2543** Run adversarial tests for repainting, missed candles, reconnect, holiday rollover, stale bid/ask, bid/ask inversion, ambiguous same-bar fills, crashes and duplicate signals. **Acceptance:** Fail closed, deterministic recovery and zero accidental broker orders.
- [ ] **FB-2544** Promote only after independent prospective paper sessions show stable positive net risk-adjusted results beyond costs and appropriately corrected baselines; otherwise keep research-only. **Acceptance:** No guarantee of profitability; all failed outcomes preserved.

### Proposed implementation boundaries
`src/financial_brain/strategies/four_node/{spec.py,indicators.py,fractals.py,state_machine.py,filters.py,exits.py,adapters.py,events.py}` (proposed, not confirmed existing); reuse `patterns/`, `kernel/`, `ingest/`, `paper/`, `forward_edge/` and existing options quote modules. Persist immutable signal, node, decision, execution and P&L evidence.
**Critical path:** FB-2501–FB-2508 source resolution → FB-2509–FB-2516 indicators/state → FB-2517–FB-2524 entry/exit → FB-2525–FB-2532 Indian adapters/options → FB-2533–FB-2544 research and evidence. Do not permit automated trading before the ambiguity gates are resolved.
**Release rule:** Eligible only for forward **paper trading** after point-in-time, non-repainting and realistic-fill tests; no physical broker orders or profitability claims without independent gates.


---

# Part B — Profitability Acceleration v2.0

# Financial-Brain — Profitability Acceleration: In-House Engineering & Evidence Master Plan

**Version:** 2.0 (incremental to Five-Desk v1.4)  
**Prepared:** 2026-11-18  
**Project:** https://github.com/crakashp2905-hub/Financial-Brain  
**Baseline:** Five-Desk v1.4 (405 pre-existing tasks). This document adds **325** executable profitability-focused tasks with unique `PA-xx-yy` IDs and does not mark old tasks complete merely because a branch has code.

## The outcome we are actually engineering

**Find, prove and safely exploit a positive *net expected and realized* trading edge, first in a paper-only system.** The engine must be able to say `NO_TRADE`, `HOLD_CASH`, `UNPROVEN`, `INSUFFICIENT_DATA` or `NO_PROFITABLE_STRATEGY_FOUND`. **No collection of checkboxes can guarantee profit.** Profitability is a market-dependent empirical finding that requires genuinely new observations and, ultimately, real fills if real-money evidence is claimed.

**In-house boundary:** All source code, experiment orchestration, forecasting models, ledgers, execution-simulation logic, reports, fixtures, audit algorithms and benchmarks should be developed and owned inside Financial-Brain, using existing open-source dependencies where appropriate. **Unavoidable external inputs:** lawful exchange/market prices, broker/market-data entitlements, effective regulatory and provider rules, future elapsed sessions, owner risk approvals and optional third-party evidence custody. Do **not** invent those inputs or bypass contracts; no external signal/alpha vendor or outsourced research is required.

### Truthful repository evidence at time of drafting

- `main`: `5c7ae82a314b...` (2026-11-16); latest full-suite commit message claims 3,368 passed / 1 skipped, but latest `main` GitHub Actions runs were reported failed (workflow jobs appeared to fail before steps). Verify precise billing/runtime root cause before assuming either a clean or broken codebase.
- Open development branches: PR #40 merges the running forward-edge branch and notes dropped F13/F17 safeguards; PR #37 → PR #36 → PR #38 carry instrument/five-desk/Four-Node work; PR #39 addresses monthly rebalance freeze and feed-outage closeout; PR #34 proposes dynamic evidence thresholds. **Do not claim these PRs have merged**.
- Five-desk v1.4 branch audit: 275/405 marked DONE, 119 PARTIAL, 2 FOOTHOLD, 9 NOT_STARTED. Audit explicitly states module survey, **not** per-task tested acceptance; much evidence sits outside `main`.
- Development research from PR #34: H005 industry momentum (+1.42%/month net *historical excess*), H003 risk-adjusted momentum (+1.28%), P3 frog-in-the-pan (+1.44%) under the proposed dynamic standard. These share a momentum factor, show drawdowns/weak recent history and are **not prospective profit proof**. P3's old post-hoc score is not an independent forward test.
- PR #38's 207-stock Four-Node 128-variant study and follow-up v2/v3 report **no after-cost successful variant**. Do not revive the family on the same historical data simply by changing filters.
- PR #38 reports 51 legacy intraday trades lacking recorded exits. PR #39 describes a stale-last-quote closeout workaround. A stale mark is not a legally/physically executable sell fill; treat exposures as unresolved until proven otherwise.
- `docs/edge-program/current-baseline.json` last audited 2026-11-13 shows **zero independently evidenced market-forward sessions and no established profitable executable forward net alpha**. Newer PR descriptions do not by themselves change that baseline.
- The repository's data-use review records unresolved permissions for mock/paper trading on Kite live quote data; obtain written clearance or operate permitted delayed/historical/fixture modes only.

**Source anchors:** [PR #34](https://github.com/crakashp2905-hub/Financial-Brain/pull/34), [PR #36](https://github.com/crakashp2905-hub/Financial-Brain/pull/36), [PR #38](https://github.com/crakashp2905-hub/Financial-Brain/pull/38), [PR #39](https://github.com/crakashp2905-hub/Financial-Brain/pull/39), [PR #40](https://github.com/crakashp2905-hub/Financial-Brain/pull/40), [main source](https://github.com/crakashp2905-hub/Financial-Brain), [checked-in baseline](https://github.com/crakashp2905-hub/Financial-Brain/blob/main/docs/edge-program/current-baseline.json).

### Economic truth contract (must be encoded as unit-tested invariants)

- For filled positions, `net_pnl = realized_close_proceeds - acquisition_cost - all execution_and_carry_costs` (for shorts/derivatives use an instrument-specific signed double-entry equivalent); **open positions** contribute marked-to-market unrealized P&L and risk, not fictional realized gains.
- `net_excess = portfolio_return_after_costs - appropriately_matching_benchmark_return` with exposure/turnover/risk controls stated separately. A model is not skillful just because a rising Nifty market generated positive rupee P&L.
- `net_EV(size) = sum_over_scenarios(probability_s × feasible_pnl_s(size)) - all_expected_costs(size)`; option scenarios include the premium path, time decay, IV/skew shifts, spreads and liquidity.
- Forecast calibration, economic expectancy, execution feasibility, risk budget and independent prospective result are **five distinct gates**. Passing one never implies another.
- A stop-loss is a *triggered intention*, not a guaranteed realized execution price. A quote mark is not a fill. A simulated path is not an independently observed market result.
- Net alpha remains uncertain: confidence bounds, clustering, multiple testing, benchmark selection and cost stress must be preregistered. **Positive numbers can fail eligibility.**

### Deliverables and definition of DONE

| Artifact | Purpose | Required proof |
|---|---|---|
| `docs/profitability/CAPABILITY_STATUS.md` | What currently works, where, and with which evidence | exact SHA + module + test + blocker |
| `docs/profitability/STRATEGY_REGISTRY.json` | Momentum, index, options, price action statuses | immutable original verdicts, version IDs |
| `docs/profitability/EXPERIMENT_CHARTERS/` | Hypotheses and evaluation design | hash-locked and dated before outcomes |
| `data/profitability/` (local; git-ignored) | Licensed data and versioned paper sessions | permitted source, immutable times and reconciled ledgers |
| `docs/profitability/RELEASE_GATE.json` | Fail-closed launch state | every condition linked to machine evidence |
| `docs/profitability/ECONOMIC_SCOREBOARD.md` | Gross/net/excess, risk, costs, fidelity | all cash days, fills, fees, open exposure |
| `docs/profitability/INCIDENTS/` | Outages, gaps, missing exits and recoveries | never fabricated reconciliation |
| `tests/profitability/` | Hostile acceptance suite | repeatable local and hosted evidence |

**Status rule:** An unchecked item means proposed or *not independently verified in the target consolidated branch*. When implementing, reuse existing code; don't build duplicate modules. Each `DONE` needs `{test, command, SHA, output file, acceptance assertion}`. Tool invocation names below are proposed targets, not assertions that the CLI exists.

### Execution order and milestones

| Milestone | Waves | Engineering output | Advancement requirement |
|---|---|---|---|
| **M0 — Truth, safety, consolidation** | W00–W05 | canonical build, lawful data, reconciled paper order/exit, complete costs | no financial invariants broken; provenance clear |
| **M1 — First serious alpha candidate** | W06–W08 | H005/H003/P3 monthly shadow/eligible paper system | preregistered after-cost comparison; no missing rebalance |
| **M2 — Options research (not presumed profitable)** | W09–W11 | calibrated index forecasts, feasible premium trades, rejected price-pattern hypotheses | independent statistical + premium economics gates |
| **M3 — Incremental research and defensible risk** | W12–W13 | mechanism-first research, portfolio sizing/kill switches | no unapproved leverage, no lookahead, robust controls |
| **M4 — Forward record & attribution** | W14–W17 | sessions, evidence, economic scorecards, failure injection | sufficient genuine sample, reconciled P&L, all operational gates |
| **M5 — Owner decision** | W18–W19 | paper release report and optional later broker evaluation | cash trading remains blocked absent separate approvals |

### Suggested initial application order

Implement the **monthly momentum book first** (H005/H003/P3), because those are the only current meaningful historical candidates. Finish end-to-end paper booking and observable exit/position accounting before expanding into a 15-minute options trading program. FVG, ORB, Fibonacci, liquidity sweep and Four-Node remain **independent hypothesis families**, never automatically profit-eligible because they match a screenshot. Mutual funds remain allocation/NAV decisions, not 15-minute SL/TP trading strategies.

---

## W00. Evidence freeze, source-of-truth and scope — P0

**Why now:** Stop conflating code, checked-in research, prospectively observed market results and independently attested execution.

**Reuse/review:** `docs/edge-program/current-baseline.json`; `docs/trials/ledger.jsonl`; `docs/audits/five-desk-v1_4/AUDIT.md` (development branch)

- [ ] **PA-00-01** Capture SHA, branch, tree hash, working-tree state, Python/OS, lockfile hash, datasource entitlements, current date and every scheduled task's checkout path; fail on an unrecorded mismatch.
- [ ] **PA-00-02** Snapshot all open PRs and their bases, branch ancestry, CI results and protected research artifacts; do not silently treat unmerged code as production capability.
- [ ] **PA-00-03** Copy the 405 v1.4 tasks into a read-only traceability index; label each code path DONE/PARTIAL/FOOTHOLD/NOT_STARTED/EXTERNAL and include tests and concrete SHA.
- [ ] **PA-00-04** Reconcile the historical trial count (13 in earlier files versus 14 in the newer survey); let immutable ledger bytes determine the truth, and annotate contradictions instead of rewriting history.
- [ ] **PA-00-05** Freeze every research outcome, historical score and post-hoc rescore with original run identity, cost assumptions, train/validation/test periods and exposure.
- [ ] **PA-00-06** Record the original strategy families, scanned indicators, rule permutations, failed candidates and the total research look budget for future multiple-testing control.
- [ ] **PA-00-07** Build a capability provenance manifest separating main, PR #34, #36/#37/#38, #39 and #40; include each source's actual commit SHA and dependency graph.
- [ ] **PA-00-08** Mark every existing P&L value as historical, historical-paper, local prospective, or independently auditable forward evidence, never a generic `profit` boolean.
- [ ] **PA-00-09** Define the five desks and their permissible actions: equities/intraday/swing, ETFs, long or defined-risk options, and NAV-based mutual funds; never force intraday execution on mutual funds.
- [ ] **PA-00-10** Declare the business objective: positive expected *and realized prospective* risk-adjusted net value after full costs, not daily positive P&L or every-signal trade frequency.
- [ ] **PA-00-11** Create a signed/hashable execution charter with owner capital budget, hard loss limits, execution mode, and permitted instruments; the model must not self-approve real cash.
- [ ] **PA-00-12** Emit a machine-readable `profit_readiness.json` listing current `RED/AMBER/GREEN` gates and links to exact evidence; reject hand-edited status percentages.

**Wave acceptance gate:** One reproducible manifest and blocker register are generated from a pinned SHA; the system cannot label any hypothetical or replayed fill as a real executable profit.

## W01. Branch consolidation, CI and release safety — P0

**Why now:** Existing main and running branches diverge; a merge can silently discard a tested invariant or change paper-order behavior.

**Reuse/review:** PR #40 (branch merge); PR #39 (feed outage); PR #37→#36→#38 (five-desk, Four-Node); PR #34 (dynamic evidence); `.github/workflows/ci.yml`

- [ ] **PA-01-01** Establish a canonical merge sequence by graph-based dependency audit; PR #37 precedes #36, and #38 depends on #36 unless deliberately rebased with a test-backed change log.
- [ ] **PA-01-02** Compare the forward-edge runner implementation in `main` versus PR #40; produce a per-public-contract `KEEP/MERGE/REIMPLEMENT/REJECT` reconciliation table.
- [ ] **PA-01-03** Explicitly preserve both forward-edge F17 instrument/data gates and F13 capability renderer if needed; PR #40 documents their omission and they must not vanish unnoticed.
- [ ] **PA-01-04** Reconcile trial governance: retain mandatory experiment charter, cohort registry, immutable trials and owner-authorized quarter budget when reconciling PR #40's branch trial policy.
- [ ] **PA-01-05** Merge equivalent order/cash reconciliation implementations into one canonical API; prohibit two simultaneous ledgers from both claiming ownership of capital.
- [ ] **PA-01-06** Re-run all historical protected-baseline SHA checks after every merge, including LF/CRLF normalization rules with canonical serialization tests.
- [ ] **PA-01-07** Resolve source inventory snapshot failure in PR #40 without loosening assertions; create deterministic cross-platform inventory generation.
- [ ] **PA-01-08** Investigate failed main CI runs and distinguish runner/billing failures from actual test failures using workflow job evidence; document root causes rather than assuming either.
- [ ] **PA-01-09** Eliminate duplicate chart-pattern CI steps; maintain an explicit `ruff`, `mypy`, migration, invariant, unit, integration, coverage, dependency-audit matrix.
- [ ] **PA-01-10** Enforce branch protection, required checks, review and CODEOWNERS where enabled; do not present a local simulated check as a hosted required check.
- [ ] **PA-01-11** Add merger integration fixtures exercising all five desks, existing zoos, closed trade reconciliation, event custody, and negative physical-order probes.
- [ ] **PA-01-12** Resolve effective scheduled checkout paths: production runner must report exact source SHA at startup and refuse running stale or unreviewed branch content.
- [ ] **PA-01-13** Add a release provenance manifest containing source SHA, test artifact hashes, changelog, strategy-rule hashes and data-rights posture.
- [ ] **PA-01-14** Add deterministic replay-before-release and rollback rehearsal; restore the prior known-good release if any net-P&L or ledger consistency invariant fails.
- [ ] **PA-01-15** Require one clean release candidate from a fresh clone with **no developer-specific data paths**; report missing external prerequisites without hiding failed test coverage.
- [ ] **PA-01-16** Keep live broker order routing physically disabled at build, config, CLI, API and integration-test layers throughout the entire profitability program.

**Wave acceptance gate:** One canonical branch runs a clean full suite in Linux and Windows, preserves prior protections, and is the branch the scheduler actually executes.

## W02. Legal data rights, point-in-time data and corporate actions — P0

**Why now:** No market edge is valid if its inputs were unavailable then, unlicensed now, or misadjusted across corporate events.

**Reuse/review:** `providers/kite_data.py`; `ingest/kite_minute.py`; `ingest/options_quotes.py`; `docs/five-desk/DATA_LICENSING_REVIEW.md` (PR #38)

- [ ] **PA-02-01** Create data-use matrix by provider/product: private research, retained history, real-time algorithmic use, derivatives depth, synthetic/replay, paper simulation, redistribution and permitted retention.
- [ ] **PA-02-02** Keep Kite live-quote paper simulation disabled or isolated until written permission resolves the documented virtual/mock-trading ambiguity; use only expressly permitted sources and modes meanwhile.
- [ ] **PA-02-03** Log source contract, evidence of approval and expiry for every entitlement; failed or expired entitlements automatically halt that datasource.
- [ ] **PA-02-04** Build a source-aware instrument master with time-varying ISIN, futures/options contract token, strike, right, lot, tick, expiry, adjustments and source timestamp.
- [ ] **PA-02-05** Reconcile 15-minute index symbols to actually optionable underlyings and currently listed contracts; unavailable contract means `NO_OPTION_TRADE`, never a generated strike.
- [ ] **PA-02-06** Deduplicate quote and candle records by instrument, event ID, venue, source clock and payload hash; preserve corrections and late arrivals instead of silent overwrite.
- [ ] **PA-02-07** Implement `exchange_ts`, `received_ts`, `available_at`, `published_at`, and `computed_at` across all relevant feeds; only available data reaches a signal.
- [ ] **PA-02-08** Detect partial candle, missing minute, wrong interval anchor, session-open gap, stale tick, nonmonotonic timestamp, crossed market and out-of-sequence event.
- [ ] **PA-02-09** Model survivorship: delisted/suspended stocks, expired option tokens, historical scheme closures, futures contract roll and ETF listing changes remain in past universes.
- [ ] **PA-02-10** Audit price-return versus total-return fields; verify dividends, splits, rights, bonus and corporate actions do not create false momentum or false reversal alpha.
- [ ] **PA-02-11** Probe suspected ex-dividend artifacts in H056/H068 without treating a repaired dataset as a valid new holdout; log changed labels and original trial verdicts.
- [ ] **PA-02-12** Build data quality reports by instrument/day: coverage, late tick rate, inconsistent OHLC, bid/ask depth validity, quote age and strike selection error.
- [ ] **PA-02-13** Add deterministic, low-cost synthetic fixture and delayed/replay feed modes for engineering tests; visually and cryptographically distinguish fixture rows from true market observations.
- [ ] **PA-02-14** Use a timezone-aware NSE calendar, effective-dated expiry and settlement tables, session halts, special sessions and instrument-specific cutoffs.
- [ ] **PA-02-15** Prove no look-ahead by mutating all source rows published after the decision and requiring byte-identical features and intended orders.
- [ ] **PA-02-16** Introduce `DATA_UNUSABLE` and fail-closed trade rejection for a missing source or quality violation; no forward-fill that pretends a quote was tradable.

**Wave acceptance gate:** Every training row, live quote, option contract and index bar has a lawful source, available-at timestamp and reproducible lineage; forbidden data use is blocked.

## W03. Reliable market-session ingestion and signal scheduling — P0

**Why now:** A paper strategy cannot be evaluated if its market run skips a rebalance, loses events or bases decisions on unobservable quotes.

**Reuse/review:** `ingest/kite_live.py`; `paper/intraday_runner.py`; `paper/day_cycle.py`; `paper/zoo_edges.py`; `forecasting/index_15m.py` (D2)

- [ ] **PA-03-01** Build one deterministic event loop for session open, completed bars, quotes, corporate actions, margin changes, decisions, protective exits and closeout.
- [ ] **PA-03-02** Separate forecasting frequency (e.g., every completed 15-minute index bar) from continuous exit evaluation; never wait 15 minutes to check a protective exit.
- [ ] **PA-03-03** Add independent heartbeats for quote feed, bars, strategy process, clock synchronization, ledger writes and position monitor; each reports last-good event.
- [ ] **PA-03-04** Prevent partial 15-minute or 1-minute candles from being marked complete; align to 09:15 IST exchange session start, not local wall-clock bucket boundaries.
- [ ] **PA-03-05** Build rate-limit-aware batching with token-bucket quotas, backpressure, subscriptions and request jitter; never infer full market coverage from one successful option-chain quote call.
- [ ] **PA-03-06** Ensure each instrument has an explicit required data profile (index level-only versus option bid/ask/depth versus MF NAV); reject missing execution data.
- [ ] **PA-03-07** Restore and verify monthly rebalance processing for H003/H005/P3 after the PR #39 missing-return exception; one bad holding cannot freeze the whole desk.
- [ ] **PA-03-08** Detect skipped scheduled runs and stale monthly books using intended-versus-actual rebalance hash comparisons; alert with exact missing signal date.
- [ ] **PA-03-09** Create data/event offsets and checkpointing so a restart resumes without duplicate signals, fills or allocations.
- [ ] **PA-03-10** Time every stage: event receipt, feature computation, model inference, risk evaluation, intended order, simulated arrival and ledger record.
- [ ] **PA-03-11** Tag each decision with `as_of`, `first_executable_after`, `expires_at`, last-good-quote-age and chosen execution method.
- [ ] **PA-03-12** Implement explicit `PAUSED`, `DEGRADED`, `RECOVERING`, `HALTED` states; failed data freshness cannot silently continue opening trades.
- [ ] **PA-03-13** Build bounded circuit-breaker recovery with exponential retry, feed priority and log deduplication; all open-position state remains durable.
- [ ] **PA-03-14** Record overnight carry and option expiry handling separately from intraday forced flatten; disallow trading after exchange cutoff.
- [ ] **PA-03-15** Keep provider calls read-only in paper mode and prove via monkeypatching/denying all broker write endpoints.
- [ ] **PA-03-16** Validate 20 consecutive local replay sessions with injected missing minutes, restarts, rate limits and market pauses; inspect event ordering and output consistency.

**Wave acceptance gate:** Replay and observed-feed runners produce identical decisions on the same timestamped event stream; missing market events produce an explicit abstention or unresolved state.

## W04. Single canonical position ledger, protective exits and reconciliation — P0

**Why now:** An attractive strategy is worthless if the ledger cannot account for an open risk, partial exit, missing fill or disconnected quote.

**Reuse/review:** `kernel/paper_account.py`; `kernel/ledger.py`; `execution/oms/`; `forward_edge/session_runner.py`; `paper/legacy_bridge.py` (PR #38)

- [ ] **PA-04-01** Enumerate existing OMS, paper books, legacy intraday runner, option spread runner and forward-edge pipeline; document one authoritative account/position ownership contract.
- [ ] **PA-04-02** Unify buy/close/sell-short/buy-to-cover, options premium and spreads, ETF shares, corporate cash actions and mutual-fund NAV-order events without losing asset-specific semantics.
- [ ] **PA-04-03** Build immutable entry, fill, partial fill, amend, stop policy, stop trigger, target trigger, cancel, expire and exit events; link to strategy and decision hashes.
- [ ] **PA-04-04** Add atomic reservations for cash, margin, risk capital, options buying power, instrument quantity, pending settlement and sellable inventory.
- [ ] **PA-04-05** Validate every transition before mutation; reject negative reserved cash, oversold quantity, ghost lots, inverted option legs and duplicate fill identifiers.
- [ ] **PA-04-06** Pair **all** open positions with an exit policy and a crash-resumable monitor; require auditable exceptions for product types like NAV-based mutual funds.
- [ ] **PA-04-07** Reconcile the 51 reported legacy intraday trades lacking recorded exits; classify independently as `OPEN_UNKNOWN`, `LEDGER_INCOMPLETE`, or confirmed close, never backfill invented fill prices.
- [ ] **PA-04-08** Treat PR #39's stale last-good quote close as a **valuation estimate** and unresolved liquidation, not an observed executable `SELL` fill; release P&L only when exit evidence exists.
- [ ] **PA-04-09** Implement stop-market and stop-limit paper semantics with bid/ask, gap-through-stop, partial fill, queue/latency and no-liquidity cases; SL is a trigger, not a guaranteed price.
- [ ] **PA-04-10** Implement mutually exclusive stop/target cancellation and replay-safe OCO simulation; handle race conditions when a fill arrives after cancellation.
- [ ] **PA-04-11** Implement trailing stops and partial take profits with total position accounting; trailing stops must be monotone in the protective direction unless policy version changes.
- [ ] **PA-04-12** Model ambiguous same-bar stop/target ordering as conservative adverse-first or `UNRESOLVED`; never choose favorable outcome after seeing the bar.
- [ ] **PA-04-13** Separate trade-exit trigger, paper execution time, observed fill, mark-to-market valuation and daily accounting close.
- [ ] **PA-04-14** Add option contract expiry, index settlement and multi-leg partial fill handling in a dedicated derivatives subledger; do not silently book an unhedged spread as defined-risk.
- [ ] **PA-04-15** Reconcile beginning equity + trades + fees + distributions + external flows + mark changes = ending equity to paise-level accounting tolerances.
- [ ] **PA-04-16** Add replay tests for lost acknowledgement, duplicated response, crash between outbox and fill, stale quote, forced EOD flatten and cross-process restarts.
- [ ] **PA-04-17** Emit an `unresolved_positions` audit and deny capital deployment when any material unknown order/position exceeds an explicit zero-tolerance gate.

**Wave acceptance gate:** Every position has one source of truth and a linked exit lifecycle; no unaccounted cash/units, fabricated closures or duplicate economic fills.

## W05. Realistic Indian execution costs, impact, sizing and capacity — P0

**Why now:** Cost leakage can turn an accurate signal into losing options or equity trading; existing backtests show this repeatedly.

**Reuse/review:** `costs/india.py`; `execution/oms/shortfall.py`; `evaluation/options_real.py`; `paper/options_runner.py`

- [ ] **PA-05-01** Create effective-dated per-product fee schedules (brokerage, exchange fees, transaction taxes, regulatory levies, GST, stamp duty, DP charges and applicable variation).
- [ ] **PA-05-02** Separate fixed per-order/per-scrip charges from bps charges; model realistic small-account lot rounding, especially ₹50k–₹2 lakh momentum books.
- [ ] **PA-05-03** Price equity and ETF buys at feasible ask-side and sells at feasible bid-side plus latency/impact; refuse midprice as executable unless separately labeled research-only.
- [ ] **PA-05-04** Capture option contract bid/ask, depth, lot size, spread percent, implied volatility, minimum tick, trading session and quote availability from permitted data.
- [ ] **PA-05-05** Model marketable-limit, passive-limit, no-fill, partial fill, gap, stop slippage and latency, retaining unfilled opportunity cost.
- [ ] **PA-05-06** Define a conservative cost floor from observed quotes, then stress +25%, +50%, +100% cost and slippage independently.
- [ ] **PA-05-07** Calibrate price impact by instrument/day/size with real order-book depth and volume participation, not a global constant everywhere.
- [ ] **PA-05-08** Flag exchange versus broker cancellation and legging assumptions separately; add legal data rights to the source fixture manifest.
- [ ] **PA-05-09** Reject strategies whose gross opportunity is smaller than spread + direct fees + expected impact + a predeclared safety margin.
- [ ] **PA-05-10** Create expected-value at the **chosen quantity**, accounting for the fact that a larger order can eliminate its own alpha.
- [ ] **PA-05-11** Derive capacity curves (INR notional vs expected impact, fill probability and mean net alpha), with a break-even capacity bound.
- [ ] **PA-05-12** Add expiry-day and near-ATM option fill stress, spread widening, volatility crush and liquidity evaporation around news events.
- [ ] **PA-05-13** Simulate stale quotes and illiquid zero-depth options as `NO_FILL`, not low-risk winning trades.
- [ ] **PA-05-14** Compare replay markouts at 5s/30s/1m/15m where lawful data supports them, to quantify adverse selection.
- [ ] **PA-05-15** Keep capital efficiency measurements: net rupees per rupee risk, max concurrent cash use, annualized turnover and net return on deployed versus total capital.
- [ ] **PA-05-16** Build account-size sweeps that include fixed charges and taxes as a separate economic view, not an artificial strategy optimization.
- [ ] **PA-05-17** Require a cost-fidelity report with provider clock, quote source, model version, stress and unavailable fields for every paper trade.

**Wave acceptance gate:** Every candidate has an executable-price distribution, complete dated charge model, downside cost scenario and maximum capital capacity; measured live costs can only tighten claims.

## W06. Scientific alpha discovery, leakage and multiple-test control — P0

**Why now:** More candidate strategies do not equal more independent opportunities; previous index, chart and Four-Node tests found no robust edge.

**Reuse/review:** `research/preregistry.py`; `evaluation/champion.py`; `evaluation/prospective.py`; `docs/trials/ledger.jsonl`; ADR-0004/0007

- [ ] **PA-06-01** Define strategy-independent immutable `ExperimentSpec` with exact hypothesis, economic mechanism, universe, clock, parameter bounds, evaluation windows, benchmark and failure criteria.
- [ ] **PA-06-02** Count **every** attempted feature, indicator, variant, time horizon, stop, take-profit, universe filter, trading rule and cost assumption as a research look.
- [ ] **PA-06-03** Keep prior 2026Q4 protected trial limits intact unless the owner explicitly authorizes a documented amendment; never grant AI authority to increase its own budget.
- [ ] **PA-06-04** Separate exploratory screening, preregistered validation, single-use holdout and future observation; hard-code access restrictions per tier.
- [ ] **PA-06-05** Treat ADR-0007 prior classes/family sizes as forward-registered decisions; retrospective re-scoring cannot retroactively certify historical results.
- [ ] **PA-06-06** Lock realistic baseline controls: cash, buy-and-hold matched index, equal-weight universe, equal-turnover random, exposure-matched and cost-matched nulls.
- [ ] **PA-06-07** Implement chronological purging/embargo for overlapping labels, shared cross-sections and intra-session 15-minute forecasts; never randomly split bar rows.
- [ ] **PA-06-08** Include all failed and delayed/no-fill signals in scoring; a system cannot discard losing signals after future prices become known.
- [ ] **PA-06-09** Estimate trade- and session-clustered uncertainty (HAC/block bootstrap) with effective independent observations, not raw overlapping trade counts.
- [ ] **PA-06-10** Use PBO / Deflated Sharpe / familywise procedures or equivalent justified correction; record chosen inference method before any result is read.
- [ ] **PA-06-11** Report two independent scientific outcomes: forecasting skill vs an honest null, and executable after-cost economic skill vs benchmark.
- [ ] **PA-06-12** Diagnose fragility by source date, instrument bucket, regime, exposure, hold duration, cost level, slippage, top names and exclusion filters.
- [ ] **PA-06-13** Add sensitivity analysis around strategy parameters without using test-period performance to select a new best parameter.
- [ ] **PA-06-14** Preserve a sealed holdout untouched by repeated manual and agent exploration; opening it requires a recorded owner authorization and one-time run ID.
- [ ] **PA-06-15** Prohibit claims of 'zero loss', guaranteed profitable trades, high accuracy = alpha, synthetic forward results or realized return manufactured from a fallback close.
- [ ] **PA-06-16** Produce machine-readable pass/fail decision with exactly which confidence, cost, null, robustness, drawdown or fidelity gate failed.
- [ ] **PA-06-17** Add automated 'stop researching this family' registry: clearly failed hypotheses stay closed unless new information or a preregistered mechanism warrants reconsideration.

**Wave acceptance gate:** A strategy cannot be promoted by selecting a pleasing backtest, retroactive prior, post-hoc filter or repeatedly opened holdout.

## W07. Candidate #1: H005/H003/P3 momentum → executable monthly book — P0

**Why now:** The strongest current evidence is in slower equity momentum; start there rather than spending the first cycle on more 1-minute chart rules.

**Reuse/review:** `paper/zoo_edges.py`; `evaluation/momentum_book.py`; PR #34 `docs/trials/dynamic/hundred/RESULTS.md`; PR #39 monthly freeze fix

- [ ] **PA-07-01** Freeze H005 industry momentum, H003 risk-adjusted momentum and P3 frog-in-the-pan as three **separate** shadow candidates with exact original formulas, data coverage and lineage.
- [ ] **PA-07-02** Prevent overlap between their research histories and later forward evidence; set a hard prospective start date from the deployed SHA, not the date a past notebook ran.
- [ ] **PA-07-03** Fix the H003 registered universe's unintended ETF/MF inclusion only by a newly versioned and separately tested rule, never silently altering H003's prior verdict.
- [ ] **PA-07-04** Require effective-dated historical index membership and delisting handling for all three; a survivorship-biased universe fails the gate.
- [ ] **PA-07-05** Reproduce H005's top-three industry ranking and subsequent stock selections from strictly data available at each monthly rebalance.
- [ ] **PA-07-06** Reproduce H003's 12–1 momentum divided by trailing 250-day volatility, with safe denominators and missing-history abstention.
- [ ] **PA-07-07** Reproduce P3's frog-in-the-pan construction without selecting a hindsight-favorable revision; document the index/universe benchmark and turnover.
- [ ] **PA-07-08** Map each book's exact holdings to purchasable equity quantities under tick, whole-share, available cash and expected charges.
- [ ] **PA-07-09** Build monthly candidate generation, next-executable-session fill planning, turnover limits, cash buffers and rejected-order logs.
- [ ] **PA-07-10** Regression-test the November 2026 monthly freeze caused by a missing future price; price-history errors must not freeze unrelated books.
- [ ] **PA-07-11** Replay one complete monthly cycle from snapshot to equity close, including dividends, transaction taxes, DP charges and partial fills.
- [ ] **PA-07-12** Track target weights, actual simulated holdings, residual cash, drift, entry/exits and actionable vs no-trade reasons.
- [ ] **PA-07-13** Score each candidate against Nifty 500 TRI or a predefined genuinely matched investable universe benchmark, with matching risk/exposure/turnover controls as appropriate.
- [ ] **PA-07-14** Measure strategy overlap, top-stock concentration, industry concentration, common drawdown and incremental alpha of combining them; do not count correlated momentum books as three independent edges.
- [ ] **PA-07-15** Analyze 2024 weakness, recent-half and last-36-month degradation explicitly; give current data higher descriptive weight but do not re-select based on a repeatedly checked holdout.
- [ ] **PA-07-16** Limit live paper capital to an owner-specified risk sleeve; model a no-trade cash outcome when available risk capital cannot cover realistic costs.
- [ ] **PA-07-17** Publish a session-by-session shadow-versus-paper ledger and monthly post-cost P&L decomposition; only prospectively filled paper positions count as paper returns.
- [ ] **PA-07-18** Require predeclared degradation/pause criteria, including deterioration vs benchmark, drawdown, observed cost stress and source quality, with no auto-relaxing to preserve candidate status.

**Wave acceptance gate:** A version-frozen momentum book makes a complete prospective rebalance and its after-cost, benchmark-relative results are auditable for every subsequent session.

## W08. Portfolio allocation and diversification without overfitting — P0/P1

**Why now:** Market exposure, correlation and overlapping signals can turn seemingly independent winners into one concentrated bet.

**Reuse/review:** `portfolio/optimizer.py`; `risk/allocator.py`; `portfolio/next_capital.py`; `forward_edge/portfolio_risk.py`

- [ ] **PA-08-01** Introduce one global account-risk budget across swing, intraday, ETF, options and MF sleeves; individual desk reports must reconcile to it.
- [ ] **PA-08-02** Define owner-editable hard limits on daily loss, rolling drawdown, per-position risk, sector/industry allocation, underlying delta and derivatives buying power.
- [ ] **PA-08-03** Compute covariance with shrinkage and guardrails for short/fat-tailed data; do not trust unstable unconstrained mean-variance weights.
- [ ] **PA-08-04** Separate diversification by independent risk driver from diversification by ticker, desk name or nominal signal count.
- [ ] **PA-08-05** Estimate each strategy's marginal contribution to portfolio expected return, variance, CVaR and expected drawdown under stress.
- [ ] **PA-08-06** Build cash-versus-trade optimization: if no candidate survives conservative uncertainty and cost, 100% cash is a valid outcome.
- [ ] **PA-08-07** Implement turnover and rebalancing budgets to prevent monthly momentum excess from being consumed by execution costs.
- [ ] **PA-08-08** Model benchmark-relative beta and size/sector/factor exposures so 'alpha' cannot merely be long beta or small-cap concentration.
- [ ] **PA-08-09** Cap overlapping Nifty index options positions as a single correlated exposure; prohibit independent sleeve sizing that doubles effective direction risk.
- [ ] **PA-08-10** Include option greeks (delta/gamma/vega/theta), payoff convexity, expiry clustering and gap-to-stop stress in portfolio scenario analysis.
- [ ] **PA-08-11** Disallow short option premium or complex multi-leg risk in a small account unless fully defined-risk, supported and explicitly authorized for paper research.
- [ ] **PA-08-12** Implement a deterministic stress harness for March-2020-scale gap analogues, liquidity freezes, exchange halts and clustered exits; label scenarios as synthetic.
- [ ] **PA-08-13** Compare equal-weight, risk-parity and conservative capped allocator out of sample with equal transaction-cost assumptions.
- [ ] **PA-08-14** Require an incremental-selection report that demonstrates why adding each candidate improves the **portfolio** after costs and complexity.
- [ ] **PA-08-15** Maintain explicit capital reserves for fees and settlement; protect against flat cash falsely appearing as tradeable buying power.

**Wave acceptance gate:** Risk budget and consolidated capital reconcile across all desks, while a newly included strategy must prove incremental rather than standalone benefit.

## W09. Fifteen-minute Nifty forecasting: prove information before trading options — P1

**Why now:** Directional accuracy at 15 minutes does not imply options alpha, and many earlier index price-only rules failed.

**Reuse/review:** `forecasting/index_15m.py` (D2); `forecasting/calibration.py`; `evaluation/index_rules.py`; `decisions/forecast_ev.py`

- [ ] **PA-09-01** Enumerate available NSE index quote history and actually optionable underlying families by effective date; do not assume every Nifty sub-index has liquid options.
- [ ] **PA-09-02** Build strict 09:15 IST session-aligned 15-minute OHLC from complete intraday feeds and prevent the currently forming bar from leaking future observations.
- [ ] **PA-09-03** Define future index return distributions at 15/30/60 minutes and close, but evaluate 15-minute trading claims separately from longer-horizon statistical relevance.
- [ ] **PA-09-04** Specify adaptive FLAT band derived only from training-period noise and trading-cost reality, not hand-tuned on profitable test outcomes.
- [ ] **PA-09-05** Create hard baselines: random walk, class frequencies, volatility-aware no-direction, same-time-of-day climatology and a simple previous-bar rule.
- [ ] **PA-09-06** Track overall three-class metrics, log loss/Brier/CRPS, directional skill, calibration per class and expected-move error; publish confidence intervals.
- [ ] **PA-09-07** Include time-of-day and liquidity regime checks: first 15 minutes, midday low activity, pre-close, known macro events, expiry days and unusual option spreads.
- [ ] **PA-09-08** Use only causal cross-index breadth, sector-relative changes and observed derivatives microstructure where data rights and timestamps allow.
- [ ] **PA-09-09** Train simple regularized linear/logistic models and gradient-boosted baselines before optional heavier sequence models; justify added complexity with held-out gains.
- [ ] **PA-09-10** Use nested chronological folds, purged overlapping forward labels and immutable train/validation/test manifests; freeze all hyperparameters before opening test.
- [ ] **PA-09-11** Build a 'forecast-issued' journal at every completed interval including probabilities UP/DOWN/FLAT, timestamp, quote freshness and model SHA.
- [ ] **PA-09-12** Score all forecasts including abstentions, missed intervals and days with feed outages; a missing outcome does not automatically become success.
- [ ] **PA-09-13** Enforce a predictive-skill gate independent of option P&L; a direction-only model cannot be promoted on apparent option profits from optimistic fills.
- [ ] **PA-09-14** Integrate an explicit market-regime/uncertainty abstention policy with diagnostics to prevent a classifier from recommending every 15 minutes.
- [ ] **PA-09-15** Keep a clean no-action baseline of simply holding cash/options avoidance; do not treat 55% directional accuracy as economic evidence without payoff analysis.
- [ ] **PA-09-16** Require an independently monitored prospective forecast sample before applying confidence as a sizing signal to paper option orders.
- [ ] **PA-09-17** Fail closed if the model produces impossible probabilities, data lag exceeds policy, a bar is partial or published events are unavailable before the decision.

**Wave acceptance gate:** A calibrated, chronologically held-out forecast clears simple baselines; if it does not, options routing returns NO_TRADE, without exceptions.

## W10. Options premium and defined-risk trade-conversion engine — P1

**Why now:** An accurate underlying index forecast still can produce a losing CE/PE trade because the option premium reflects more than direction.

**Reuse/review:** `paper/options_runner.py`; `paper/options_spread.py`; `execution/option_book.py` (D2); `signals/index_options.py` (D2); `evaluation/options_real.py`

- [ ] **PA-10-01** Create a canonical NFO contract ID from exchange, underlying, right, strike, expiry and effective token; refuse any guessed or delisted instrument.
- [ ] **PA-10-02** Parse permitted live options-chain data: bid/ask, depth, OI, traded quantity, last trade age, implied volatility and lot size by contract.
- [ ] **PA-10-03** Build volatility-surface sanity checks for strike/expiry, no-arbitrage bounds and stable estimation when quotes are wide or missing.
- [ ] **PA-10-04** Implement independently tested Black–Scholes/Black-76 or suitable local pricing approximations for explanatory Greeks; price paper fills from actual quotes, never only theoretical midpoints.
- [ ] **PA-10-05** Scenario-simulate option premium under underlying move, time passage, volatility change, skew shift, expiry and liquidity widening.
- [ ] **PA-10-06** For a 15-minute direction prediction, compute the *premium* expected P&L distribution, not the index points multiplied by option delta alone.
- [ ] **PA-10-07** Evaluate candidate CE, PE and supported defined-risk debit spreads with the same explicit fee, quote, size and no-fill constraints.
- [ ] **PA-10-08** Add an executable contract ranking with selected/no-selected explanations: delta, break-even move, gamma exposure, theta, spread, depth, maximum loss and risk-capped quantity.
- [ ] **PA-10-09** Reject illiquid near-expiry premium where the market spread or tail gap can exceed expected alpha, even if direction confidence is high.
- [ ] **PA-10-10** Keep directional index hypothesis and option execution hypothesis as distinct signals/IDs; both require evidence, and neither inherits the other's performance score.
- [ ] **PA-10-11** Implement premium-based protective stop/target/expiry exit as separate from underlying invalidation; trigger at observed tradeable quotes with partial-fill handling.
- [ ] **PA-10-12** Add long-option payoff bounds: maximum invested premium loss plus transaction costs; detect capital at risk after partial fills or cancellations.
- [ ] **PA-10-13** For debit spreads, model asynchronous legs, per-leg spread and no-fill; reject if portfolio could briefly become an unsupported naked short.
- [ ] **PA-10-14** Compare BUY CE/PE against cash and a simple delta-equivalent exposure benchmark; choose none when expected option alpha does not clear stress.
- [ ] **PA-10-15** Store rejected candidate options along with the best observed quote and future counterfactual outcome using legally permitted data, not retrospective executable claims.
- [ ] **PA-10-16** Build entry/exit state for stop, target, time stop, volatility shift, signal reversal and EOD; pre-register exit rule and cost assumptions.
- [ ] **PA-10-17** Measure profit per underlying-direction forecast, per option setup, expiry, delta bucket and quote-spread bucket; identify where correct direction loses money.
- [ ] **PA-10-18** Conduct targeted prospective option paper sessions with precommitted contracts, bid/ask evidence, timestamps, fill probability and reconciled net P&L before any real-money readiness claim.
- [ ] **PA-10-19** Keep short-premium naked selling explicitly disabled; future defined-risk credit strategies require separate contract/margin/legal review and dedicated validation.

**Wave acceptance gate:** Every options paper recommendation identifies an observed contract and produces a quote-feasible entry/exits, net premium P&L and a defensible NO_TRADE outcome.

## W11. Price-action family: FVG, ORB, sweep, Fibonacci, E1/E2, Four-Node — P1

**Why now:** Screenshots suggest hypotheses but do not establish preknown signal times, executable pricing or profitability.

**Reuse/review:** `patterns/`; `evaluation/intraday_family.py`; `strategies/four_node` (PR #38); v1.4 section 25; screenshot hypothesis set

- [ ] **PA-11-01** Preserve Four-Node v1 (128 variants), v2 and v3 results as failed evidence and keep their original source and parameter snapshots immutable.
- [ ] **PA-11-02** Refrain from optimizing Four-Node on the same 2016–2026 samples until a new preregistered economic mechanism and untouched validation source exist.
- [ ] **PA-11-03** Implement FVG/IFVG from precisely defined three-candle non-overlap rules with availability-at time and invalidation; avoid visual hindsight zones.
- [ ] **PA-11-04** Implement a liquidity sweep with a previous confirmed high/low, threshold crossing, completed reclaim and bounded confirmation latency.
- [ ] **PA-11-05** Implement five-minute and 15-minute opening range breakout with known session-open clock, close-through, buffer, volume gate and invalidation.
- [ ] **PA-11-06** Implement Fibonacci levels only with causally confirmed anchors; formally test whether arbitrary extensions have value beyond generic support/resistance.
- [ ] **PA-11-07** Implement continuation pullback and 20C moving-average filters with exact Wilder SMMA recurrence, lookback initialization and no-lookahead pivots.
- [ ] **PA-11-08** Implement E1/E2 scaling as two distinct conditional intents linked to one maximum account risk, protecting against correlated doubling of loss.
- [ ] **PA-11-09** Implement N1–N4 pivot confirmation timestamps and trade-time snapshots; chart annotations that appear later must never create earlier entries.
- [ ] **PA-11-10** Build visual-audit golden fixtures for every screenshot-inspired setup: candle times, known-at overlays, entry, SL, TP and condition failures.
- [ ] **PA-11-11** Compare each proposal against simple breakout, reversal and same-time random controls before introducing machine learning or multiple confirmation indicators.
- [ ] **PA-11-12** Evaluate on tradable Indian equity/ETF/futures instruments first; map a surviving underlying rule into options only with a separate premium P&L study.
- [ ] **PA-11-13** Count family parameter sweeps, timeframe changes, stop variants and selected screenshot examples as research looks; keep the protected trial budget.
- [ ] **PA-11-14** Require walk-forward, cash/benchmark, after-cost, random-entry/null, data quality and sensitivity gates before eligibility beyond SHADOW.
- [ ] **PA-11-15** Explicitly retire strategies that fail; a strategy catalogue may retain a useful visual detector without being allowed to advise a trade.

**Wave acceptance gate:** A non-repainting independent study either rejects or advances a *new* family under its research budget; previously failed Four-Node variants remain failed.

## W12. New informational alpha with an economic mechanism — P1

**Why now:** The historical record largely rejects price-only intraday signals; spend new research capacity on independent information or cost advantages.

**Reuse/review:** `events/`; `docintel/`; `ingest/xbrl_results.py`; `regime/`; `brain/07-Research/`; `evaluation/`

- [ ] **PA-12-01** Create an 'alpha hypothesis ledger' with mechanism, who trades against whom, why information is delayed/ignored, expected horizon and what would falsify it.
- [ ] **PA-12-02** Prioritize independent slower-horizon research on filings/events, industry spillovers, volatility/quality, institutional behavior and changing market structure based on existing lawful feeds.
- [ ] **PA-12-03** Repair release/publication timestamps for company results, insider trades, ownership disclosures, revisions, conference calls and macro events.
- [ ] **PA-12-04** Validate text-extracted facts against source bytes and official disclosures; no LLM-generated accounting number enters a trading feature without provenance and deterministic checking.
- [ ] **PA-12-05** Build 'market reaction already priced?' controls: opening gap, post-publication delay, overnight release, turnover shock and day-of-week benchmarks.
- [ ] **PA-12-06** Compare event-driven selection against momentum overlap and matched sector/size exposures, not raw market-return drift.
- [ ] **PA-12-07** Investigate high-cost avoid-filters separately from equity alpha and from operational risk filters; report if excluding names changes turnover and fees more than returns.
- [ ] **PA-12-08** Build event-to-trade simulator accounting for detection delay, source publication, broker arrival, exchange opening and unavailable after-hours execution.
- [ ] **PA-12-09** Explicitly handle disclosure coverage gaps and missing older history; do not backfill derived news sentiment with modern sources and claim point-in-time availability.
- [ ] **PA-12-10** Use the existing knowledge graph as a research feature only when edges are dated, typed and auditable, not as a free-form LLM trading signal.
- [ ] **PA-12-11** Test residualized factors that target independent excess beyond momentum/low volatility; retain the residual signal's reduced effect honestly.
- [ ] **PA-12-12** Quantify opportunity frequency and market capacity before building complex models; a statistically detectable event with negligible executable volume is not a product.
- [ ] **PA-12-13** Require out-of-sample replication across market phases, instrument sizes and data acquisition paths before allocating even paper capital.
- [ ] **PA-12-14** Introduce an annualized research-budget ledger with per-family attention costs and a hard stop on redundant 1-minute/indicator combinations.
- [ ] **PA-12-15** Favor improving decision abstention, allocation and execution of a real signal over attempting to force 25 independent strategies to exist.
- [ ] **PA-12-16** Keep an explicit `NO_REPLICABLE_ALPHA_FOUND` outcome; no optimization objective is allowed to rewrite this into a favorable verdict.

**Wave acceptance gate:** New hypotheses are mechanism-first, preregistered, genuinely distinct from failed price patterns, and do not cannibalize the sealed holdout.

## W13. Sizing, portfolio safety, strategy degradation and circuit breakers — P0

**Why now:** Survival precedes growth; position sizing must reflect uncertainty, clustered losses, premium convexity and a failure to exit.

**Reuse/review:** `risk/allocator.py`; `risk/degradation.py`; `risk/killswitch.py`; `execution/oms/risk_gate.py`; `forward_edge/harm_stop.py` (forward branch)

- [ ] **PA-13-01** Require fixed initial owner-defined capital, risk-per-trade, daily/weekly/monthly max drawdown, max contract exposure and maximum acceptable slippage in a signed policy.
- [ ] **PA-13-02** Compute risk from worst credible simulated loss at the intended **position size** plus fees, rather than stop distance alone.
- [ ] **PA-13-03** Require a hard cap on aggregate Nifty-direction delta even when the positions are in different indices, sectors or strategies.
- [ ] **PA-13-04** Model option Greeks and tail gap loss at portfolio level; account for same-underlying multi-leg concentration and correlated settlement risk.
- [ ] **PA-13-05** Use volatility-aware position sizing with severe shrinkage toward zero when probabilities are uncalibrated or recent evidence weakens.
- [ ] **PA-13-06** Restrict Kelly-like sizing to audited forward net outcomes, conservative shrinkage and hard fraction caps; never infer optimal size from in-sample win rate.
- [ ] **PA-13-07** Add order count, order value, turnover, failed execution, stale-data and account-loss brakes independent of strategy signal generation.
- [ ] **PA-13-08** Automatically abstain on known unusable exchange states, unlicensed data, missing underlying, invalid option contract, stale quotes and uncertain open exposure.
- [ ] **PA-13-09** Simulate slippage tail events where stop fills far beyond intended level and margin depletion may be larger than initial quoted stop risk.
- [ ] **PA-13-10** Add crash-safe kill switches for broker and paper engines, with external owner reset required after risk breach; AI recommendations cannot override a halt.
- [ ] **PA-13-11** Detect performance drift using calibrated rolling metrics and sequential monitoring that controls false alarms from repeated peeking.
- [ ] **PA-13-12** Define a first-loss incident postmortem workflow: preserve raw quote, signal, risk, execution and ledger hashes before any attempted repair.
- [ ] **PA-13-13** Ensure rejected and stopped strategies retain outcome evidence for postmortem and retraining without turning rejected fills into observed market fills.
- [ ] **PA-13-14** Test historical stress years, option spread blowouts, settlement events, split errors, mass data outages and platform/clock failures independently.
- [ ] **PA-13-15** Run property-based simulation of thousands of trades ensuring total reserved and at-risk capital never exceeds signed hard limits.
- [ ] **PA-13-16** Maintain multiple distinct risk declarations: forecast uncertainty, execution uncertainty, liquidity risk, market gap risk and operational position-state uncertainty.

**Wave acceptance gate:** Bounded risk holds under severe gap, stale feed and multiple simultaneous exit conditions; the engine accepts `NO_TRADE` without human intervention.

## W14. Prospective paper evidence, custody and repeatable full sessions — P0

**Why now:** A 10-year backtest cannot substitute for decisions frozen before future market prices are observed.

**Reuse/review:** `forward_edge/session_runner.py`; `forward_edge/decision_custody.py`; `forward_edge/evidence_stats.py`; `paper/decision_journal.py`

- [ ] **PA-14-01** Establish separate SHADOW and LIVE_PAPER cohorts with different IDs, policy hashes, execution assets and capital; disallow hindsight promotion of a shadow result into prospective execution evidence.
- [ ] **PA-14-02** Check legal scope of real-time data paper trading before prospective live paper capture; if not permitted, stay in delayed/replay evidence and say forward executable testing is blocked.
- [ ] **PA-14-03** Lock exact strategy code, parameters, universe, signal interval, costs, risk and benchmark before each prospective session starts.
- [ ] **PA-14-04** Write append-only local intent/decision/quote/outcome records with source clocks and SHA-linked previous event; treat local hashes as tamper-evident only, not independent proof.
- [ ] **PA-14-05** Use independent third-party custody/timestamp receipts only when legally available, else label evidence `LOCAL_ONLY`, never fabricated `ATTESTED`.
- [ ] **PA-14-06** Preserve every interval's recommendation including NO_TRADE, rejected contract, skipped rebalance and missing data; zero trades is not a profitable trading day.
- [ ] **PA-14-07** Keep true session return series including zero-return cash days and partial positions; never filter the P&L sample down to profitable sessions.
- [ ] **PA-14-08** Require every candidate to publish its expected-value forecast **before** the execution clock and record changes as new versions, not retroactive annotation.
- [ ] **PA-14-09** Persist real quote bid/ask and execution-data availability at decision and exit when rights permit; no closing price fallback can claim execution.
- [ ] **PA-14-10** Automate day-end reconciliations of orders, positions, cash, open risk, fees and marked equity; seal a run only after all exceptions are resolved.
- [ ] **PA-14-11** Maintain a separate unresolved-exposure journal: stale price, missing exit, no confirmable quote and broker/fill ambiguity are adverse evidence, not zero-loss exceptions.
- [ ] **PA-14-12** Add shock drills with feed loss, process restart, system clock skew, option expiry, canceled orders and unmapped tokens during a paper session.
- [ ] **PA-14-13** Create an independent daily reporting job that verifies all decisions and quote lineage from raw logs, without importing research/backtest targets.
- [ ] **PA-14-14** Generate one-page session proof with eligible count, generated signals, blocked signals, matched fills, missing fills, gross/net P&L, worst loss and benchmark comparison.
- [ ] **PA-14-15** Keep portfolio performance and evidence separated by asset class, strategy, origin (manual/automatic), forward regime and realized versus valuation components.
- [ ] **PA-14-16** Reproduce the entire session and compare deterministic event hashes to original run, including abstentions and unknown outcomes; fail on material mismatch.
- [ ] **PA-14-17** Require sequential evidence windows specified in advance; do not stop and declare success on a lucky streak while continuing to test after losses.
- [ ] **PA-14-18** Set trading-day operational checks that are measurable without profits: zero unaccounted open orders, zero unsupported fills, zero unauthorized broker calls, source freshness pass and complete outputs.
- [ ] **PA-14-19** Create a release-blocking `forward_evidence_gate` that remains RED until all specified clean future sessions, attribution and benchmark superiority are genuinely observed.

**Wave acceptance gate:** A full uninterrupted lawful market session can be reproduced from raw captured observations with every decision made before outcome and every fill/unknown recorded correctly.

## W15. Performance attribution, forecast skill and counterfactual decision analysis — P0/P1

**Why now:** Profit comes from something specific: selection, direction, sizing, holding period, timing, financing or luck. We must know which.

**Reuse/review:** `paper/daily_results.py`; `paper/loss_review.py`; `decisions/net_alpha.py`; `evaluation/scorecards.py`

- [ ] **PA-15-01** Calculate actual filled-trade P&L from entry proceeds, closing proceeds, commissions, spreads, funding and other appropriate charges, with a separate tax-estimate view.
- [ ] **PA-15-02** Reconcile closed trade outcomes and still-open mark-to-market risk into ending account equity; do not sum only winners or ignore partial exits.
- [ ] **PA-15-03** Attribute returns to market beta, industry, style, selection, timing, position size, regime, option premium effects, liquidity and execution quality.
- [ ] **PA-15-04** Decompose options P&L by underlying movement, delta/gamma approximations, theta decay, implied-volatility change and residual bid/ask execution.
- [ ] **PA-15-05** Separate predicted index UP/DOWN/FLAT accuracy from whether the option position actually generated positive net premium return.
- [ ] **PA-15-06** Compare strategy to matching benchmark with exposure, sector, leverage and turnover controls; raw absolute profit in a bull market does not establish alpha.
- [ ] **PA-15-07** Decompose slippage relative to decision mid, executable touch, arrival quote and observed paper-fill model, disclosing which reference points are hypothetical.
- [ ] **PA-15-08** Build accepted-vs-rejected candidate counterfactuals only where comparable observation data exists; label those valuations as estimates, not executable fills.
- [ ] **PA-15-09** Track turnover, net basis points per trade, drawdown duration, worst day/week/month, loss clustering, tail loss, Sharpe/Sortino, exposure and capacity.
- [ ] **PA-15-10** Compute per-trade expectancy and break-even win probability given actual win/loss distribution and all costs; no universal '60% win rate' target.
- [ ] **PA-15-11** Report realized versus unrealized P&L and available cash, then month-to-date and cumulative performance with contributions by strategy/desk/instrument.
- [ ] **PA-15-12** Create a model confidence calibration report by regime and time of day; a 70% probability bin must be empirically checked on unseen observations.
- [ ] **PA-15-13** Measure marginal contribution of each signal/confirmation filter through predeclared ablation and equal-cost nulls, counting all research looks.
- [ ] **PA-15-14** Score approval/discretion separately: owner overrides vs default model under identical point-in-time information and capital budget.
- [ ] **PA-15-15** Implement trade-level loss review containing data, model, contract choice, execution, exit and sizing root cause; no narrative without source references.
- [ ] **PA-15-16** Export a tamper-evident account report that distinguishes research, paper-fills, hypothetical scenarios, and broker-verified activity.
- [ ] **PA-15-17** Regress net results on cost multipliers and market exposure; flag any 'profit' depending solely on optimistic fills or extraordinary beta.

**Wave acceptance gate:** Every rupee and every claim is traceable to transactions and marked positions; diagnostics reveal if any advantage survives execution friction.

## W16. Trader control room, actionable signals and operational trust — P1

**Why now:** A working trading platform must communicate what is known, what it recommends, what can be traded and what remains unresolved.

**Reuse/review:** `paper/control_room.py` / `paper/dashboard_html.py` (D2); `paper/trader_view.py`; `forward_edge/cockpit.py`

- [ ] **PA-16-01** Build a five-desk dashboard with source SHA, live/fixture label, newest quote age, risk policy, holdings, free cash, daily and historical results.
- [ ] **PA-16-02** Implement a synchronized Nifty direction grid with p(UP)/p(DOWN)/p(FLAT), expected range, model validity and forecast-vs-actual calibration status.
- [ ] **PA-16-03** Add explicit `BUY`, `SELL_TO_CLOSE`, `BUY_TO_COVER`, `HOLD`, `WATCH`, `NO_TRADE`, `REJECTED`, `UNRESOLVED` states instead of conflating forecast with order.
- [ ] **PA-16-04** Display real contract identity, expiry, strike, right, lot, bid/ask source, decision time and quote age next to every CE/PE proposal.
- [ ] **PA-16-05** Display SL and TP as *policy triggers* and subsequently observed actual exits as distinct fields; a stop label never implies an executable exit price.
- [ ] **PA-16-06** Show per-trade ex-ante expected costs, fee uncertainty, stop distance, quantity and blocked reason; make `NO_TRADE` a first-class visible result.
- [ ] **PA-16-07** Provide per-strategy and per-desk gross P&L, fee leakage, net P&L, risk-adjusted benchmark excess and independent evidence tier.
- [ ] **PA-16-08** Show a red operational banner for missing exits, unresolved positions, stale quotes, stale rebalance, missing data permissions and failing CI.
- [ ] **PA-16-09** Expose immutable signal-to-order-to-fill-to-exit drill-down with traceability hashes and one-click comparison to exact past snapshot.
- [ ] **PA-16-10** Implement conservative alerts for newly generated eligible signals, existing-position SL/TP triggers, risk/kill-switch state changes and data-health failures.
- [ ] **PA-16-11** Include a research-hypothesis board with strategy version, historical verdict, forward tier, prior failed variants and reasons for non-promotion.
- [ ] **PA-16-12** Build a simple operator replay mode for a specific session that animates decisions without showing candles that were not yet known at that instant.
- [ ] **PA-16-13** Use usability tests with false-positive BUY and stale-option-quote cases; an operator must correctly distinguish recommendation from executable position.

**Wave acceptance gate:** The interface cannot display a fabricated entry, filled exit, current return, market open status or promotion eligibility.

## W17. Hostile validation and non-negotiable quality gates — P0

**Why now:** A complex system should fail safely even when its models, sources, clocks or local software are wrong.

**Reuse/review:** `tests/`; `.github/workflows/ci.yml`; `forward_edge/falsification.py` (forward branch); `patterns` adversarial suite

- [ ] **PA-17-01** Add test fixtures for same-bar SL+TP, gap-through-stop, no exchange liquidity, partial option fills, closing quote older than the stop trigger and market halt.
- [ ] **PA-17-02** Build clock-leakage tests for 1m/5m/15m/futures/options/MF data, all with publication-time and available-at mutations.
- [ ] **PA-17-03** Test corporate action and index membership lookahead using historical ticker/ISIN changes, delisted symbols and revised filings.
- [ ] **PA-17-04** Fuzz options chain contract identity, negative/zero bid, crossed quotes, infinity/NaN Greeks, expired token, wrong lot size and wrong option right.
- [ ] **PA-17-05** Run property-based accounting tests for cash, units, gross/net book, charge conservation, dividend, split, option settlement and partial exits.
- [ ] **PA-17-06** Simulate state-machine crashes before/after every durable write, after quote but before intent, before/after fill and during ledger reconciliation.
- [ ] **PA-17-07** Test all loss-limit gates under concurrent desk decisions; ensure a risk block cannot be overridden by the UI, agent, broker adapter or reentrant event.
- [ ] **PA-17-08** Reproduce monthly-book November freeze, 51 missing exits, source-inventory CRLF issue, F13/F17 regression and nonmonotonic events as permanent regression tests.
- [ ] **PA-17-09** Include quote-feed outage and recovery scenarios with a position that remains open: stale valuation must not be converted into a fabricated closed fill.
- [ ] **PA-17-10** Add an explicit no-broker-write test intercepting network and all order client functions; every intended paper trade must remain local.
- [ ] **PA-17-11** Run strategy acceptance with surrogate labels and permuted returns to prove the pipeline does not 'discover' strong alpha from random noise.
- [ ] **PA-17-12** Verify candidate confidence calibration and economic expectancy independently with known randomized experiments and fixed seeds.
- [ ] **PA-17-13** Ensure no strategy promotion on validation-only datasets, training-only holdout, post-hoc prior assignments or false 'independent' custody labels.
- [ ] **PA-17-14** Maintain clean Linux and Windows CI, reproducible dependencies, isolated DB fixtures and source hashing with coverage targets for critical financial invariants.
- [ ] **PA-17-15** Run differential tests between historical replayer and live event stream for identical ordered inputs, requiring identical strategy decisions and risk rejections.
- [ ] **PA-17-16** Include performance/load tests for simultaneous 15-minute index forecasts, options chain evaluation and protective exits while the broker interface is denied.
- [ ] **PA-17-17** Maintain an incident classification and fix-or-disable policy; a new unresolved monetary invariant blocks release regardless of green strategy metrics.
- [ ] **PA-17-18** Require tests, docs and an executable acceptance artifact per checklist item; no checkbox may be marked DONE solely because a module exists.

**Wave acceptance gate:** A clean-host automated test suite blocks every material money/custody/forecast error and all previously encountered operational incidents.

## W18. Deployment, rights, release and eventual real-money safety — P0/P1

**Why now:** Research success is not authorization to send real orders; regulatory, broker, operational and owner restrictions remain independent.

**Reuse/review:** `execution/oms/rollout.py`; `forward_edge/modes.py`; `.github/workflows/ci.yml`; `docs/RUNBOOK-LIVE.md`

- [ ] **PA-18-01** Provide reproducible Windows and Linux installations from an audited lockfile with zero user-specific hard-coded paths.
- [ ] **PA-18-02** Configure one local scheduler for exchange-aware daily preflight, 15-minute forecasts, protective exit monitoring, monthly rebalances and day-end reports.
- [ ] **PA-18-03** Implement encrypted credential storage and secret scanning; remove broker API secrets from logs, diagnostics, export files and screenshots.
- [ ] **PA-18-04** Bind live paper datasource entitlements and permitted retention to an explicit source policy and audit trail; reject unapproved live mock usage.
- [ ] **PA-18-05** Add backup, encrypted ledger export, point-in-time restore, disaster recovery and independent verification of account balances after restore.
- [ ] **PA-18-06** Publish strategy and account kill-switch conditions in a read-only ops runbook; make failures and degraded modes obvious to operators.
- [ ] **PA-18-07** Require the real-market/live broker path to stay unavailable in all shipping artifacts for this phase, regardless of configuration toggles.
- [ ] **PA-18-08** Implement an **offline** broker dry-run verifier for order idempotency, pretrade compliance, margin preflight and required approval without any network order write.
- [ ] **PA-18-09** Identify current Indian exchange/broker/SEBI rules for retail automated orders and provider data use from official sources before contemplating an actual broker connection.
- [ ] **PA-18-10** Require independent written provider answers about quote retention, live paper use and derivatives-data permissions; do not claim an all-in-house replacement for licensed data.
- [ ] **PA-18-11** Require explicit human approval, capital source, loss limits, freeze windows and exit emergency procedures separately from the algorithm's validation score.
- [ ] **PA-18-12** If cash trading is later authorized, start with the smallest practical legal brokerage lot/position and a human-approved ticket stage; avoid naked option writing and unsafe leverage.
- [ ] **PA-18-13** Maintain an independent broker post-trade reconciler for any later live account, and refuse resend after unknown acknowledgement until order status is verified.
- [ ] **PA-18-14** Implement staged rollback and transaction-cancel safety; changing a paper strategy cannot silently mutate a live-account release.
- [ ] **PA-18-15** Maintain public-facing claim labels so users never mistake a backtested forecast, a local paper result and real broker profit for equivalent evidence.
- [ ] **PA-18-16** Disallow production launch if any data-rights, compliance, liquidity, cash/positions, unresolved orders or forward-evidence gate is red.

**Wave acceptance gate:** A staged paper release is reproducible and safe, with real trading inaccessible until independently documented owner/broker/regulatory gates are met.

## W19. Proof of economic profitability and go/no-go release — P0

**Why now:** The only meaningful endpoint is an independently understandable sequence of future results with a credible after-cost advantage and controlled risk.

**Reuse/review:** `forward_edge/evidence_stats.py`; `evaluation/scorecard.py`; `docs/edge-program/current-baseline.json`; future `profitability/` modules

- [ ] **PA-19-01** Define a minimum acceptable economic outcome **before** prospective observation: positive portfolio net excess after execution costs with a dependence-aware lower uncertainty bound above zero or another justified predeclared threshold.
- [ ] **PA-19-02** Define the benchmark per desk and horizon: matched equities TRI, cash/short-term safe benchmark, ETF benchmark or an honest options risk/capital alternative; never use one benchmark for incompatible assets.
- [ ] **PA-19-03** Define a minimum number of independent *sessions or rebalance periods*, not just an inflated count of overlapping 15-minute bars or highly correlated trades.
- [ ] **PA-19-04** Lock risk acceptance in advance: maximum peak-to-trough loss, worst scenario loss, operational error tolerance, concentration, quote delay and exposure limits.
- [ ] **PA-19-05** Require net excess survives optimistic/base/adverse fee and execution-cost models; if it wins only under optimistic midprice fills, mark FAIL.
- [ ] **PA-19-06** Require the selected candidate improves on a simple, implementable baseline with comparable cash allocation, turnover, beta, factor tilt and risk; otherwise no new capital is warranted.
- [ ] **PA-19-07** Require no unclosed/unresolved economic exposures, cash mismatches, fabricated closes, unknown order outcomes or missing broker permissions as part of the profit score.
- [ ] **PA-19-08** Evaluate out-of-sample calibration for forecasts separately from actual option premium P&L; false confidence must reduce position sizes and trigger abstention.
- [ ] **PA-19-09** Compare profit across market regimes and independent calendar blocks; a gain concentrated in one event or one day is insufficient.
- [ ] **PA-19-10** Publish raw equity series, all fee lines, all trades, rejected opportunities, open marks, dated data-source access and source hashes for reproducibility.
- [ ] **PA-19-11** Create a 'decision audit' explaining why the same candidate would or would not be recommended with only data available at that exact time.
- [ ] **PA-19-12** Classify each family as `REJECTED`, `SHADOW`, `PAPER_UNPROVEN`, `PAPER_PROMISING`, `PAPER_CONFIRMED`, `CASH_REVIEW_ONLY`, or `BROKER_APPROVAL_ELIGIBLE` using frozen gates.
- [ ] **PA-19-13** Make `NO_PROFITABLE_STRATEGY_FOUND` a valid release output; system reliability may reach 10/10 while economic alpha is absent.
- [ ] **PA-19-14** Require forward degradation monitoring after initial success, with rollback and deallocation if net value disappears; do not freeze a historical profitable label permanently.
- [ ] **PA-19-15** Produce a final owner-facing economic report: what made money, when, why, how much was lost, how robust results are and why actual cash remains gated.

**Wave acceptance gate:** Results are judged against frozen acceptance criteria; PASS, FAIL and INSUFFICIENT_EVIDENCE are all legitimate and cannot be manually overwritten by the research agent.

## Proposed file ownership (targets; inspect before creating)

- `src/financial_brain/profitability/registry.py`: immutable candidate status, lineage and gate reasons.
- `src/financial_brain/profitability/preflight.py`: branch, sources, scheduler, clock, permissions and release status.
- `src/financial_brain/profitability/economics.py`: net costs, matching benchmarks, EV and uncertainty contracts.
- `src/financial_brain/profitability/reconcile.py`: account/position/exit integrity; reuse canonical OMS.
- `src/financial_brain/profitability/candidates.py`: H005/H003/P3 wrappers (do not duplicate underlying algorithm implementations).
- `src/financial_brain/profitability/forward_gate.py`: independent prospective evidence and release admission.
- `src/financial_brain/profitability/scoreboard.py`: fee-to-alpha decomposition and status views.
- `tests/profitability/`: unit, property, simulation, release and adversarial gates.

**Proposed CLI**, not existing commands:

```text
fb profit audit --as-of YYYY-MM-DD
fb profit strategy-status --all --with-evidence
fb profit preflight --paper-only
fb profit reconcile --session YYYY-MM-DD --strict
fb profit candidates --family momentum --mode shadow
fb profit options --index NIFTY --forecast-horizon 15m --mode research
fb profit forward --paper --cohort momentum-v1
fb profit evidence --candidate H005 --show-blockers
fb profit scorecard --net --matched-benchmark --cost-stress
fb profit release-gate --strict
```

## Mandatory acceptance tests before declaring progress

1. **No alpha fabrication:** 100% random/no-skill candidate inputs do not promote when properly evaluated under preregistered familywise procedures; a winning chart example is not a proof.
2. **No bogus options win:** a +index directional forecast with option premium losing on IV/theta is marked a loss, not success.
3. **No missed position:** feed dies with an open position; no stale last-quote paper SELL is recorded as an executed close.
4. **No phantom capital:** restart during order commit, partial fill, OCO cancellation or monthly rebalance never duplicates cash, quantity or P&L.
5. **No market lookahead:** mutated future candles, revisions, filings and next-bar pivots cannot change already-issued signals.
6. **No cost bypass:** failed cost stress, fixed-fee impact, wide option spread and latency automatically reject rather than revise entries.
7. **No false claims:** replay, proxy quotes and local-only custody remain labeled; no agent can self-promote to `PAPER_CONFIRMED` or `LIVE_CASH`.
8. **No forced trade:** system can produce all NO_TRADE for a session without raising success-rate alarms or inventing a position.
9. **No hidden leverage:** multiple Nifty and BankNifty positions plus futures and correlated equities obey one account-level risk budget.
10. **No survivorship artifact:** a previously delisted equity, ETF corporate action and expired option cannot disappear from historical losses.

## Final outcome contracts

**Engineering-complete ≠ profitable.** The in-house build can be fully implemented even if no profitable strategy exists. Promote a strategy only when *its own frozen preregistered metrics* prove after-cost advantage on new independent data and it passes legal, liquidity, accounting, drawdown and risk tests. If not, explicitly return `INSUFFICIENT_EVIDENCE`, `FAILED` or `NO_PROFITABLE_STRATEGY_FOUND`, then continue only with a documented new research hypothesis.

**Real-money gate:** independent forward evidence is necessary but not sufficient. Real orders require current market/broker rules, source permissions, account controls, real fill calibration and explicit human approval. No action in this document authorizes physical trading.


---

# Part II — New Self-Learning / Reinforcement-Learning / Autonomous Alpha Discovery Plan

# Financial-Brain 3.0 — Self-Learning, Reinforcement Learning & Autonomous Alpha Discovery Master Implementation Plan

**Target repository:** https://github.com/crakashp2905-hub/Financial-Brain  
**Prepared:** 2026-11-21 (Asia/Kolkata)  
**Scope:** first-party, end-to-end autonomous **research and shadow/paper learning** for five Indian-market desks.  
**Status:** forward-looking engineering specification; an unchecked task is **not** proof that no corresponding code already exists. Reuse and reconcile existing code before implementing.  
**Core objective:** maximize **out-of-sample, after-cost, risk-constrained economic value** over long horizons, and discover/retire independent strategies with quantified uncertainty. **No system can guarantee a profit or a particular trade win rate.**

## Executive implementation decision

Do **not** make an unconstrained self-modifying LLM or deep RL algorithm the production trading engine. Build an audited hierarchy:

```text
Authoritative point-in-time market + portfolio event store
                   |
        Evidence & permission firewall
                   |
        Economic hypothesis generator
                   |
        Deterministic strategy DSL compiler
                   |
          Pre-registration / trial budget
                   |
      Causal feature + trajectory builders
                   |
     Base policies -> contextual bandits -> conservative offline RL
                   |
     Independent falsifier / OPE / nested walk-forward
                   |
      Frozen champion-challenger shadow cohorts
                   |
          Prospective evidence & capital gate
                   |
    Deterministic external RiskGovernor & OMS boundary
                   |
      Reconciled paper ledger + outcomes + drift
                   |
  Delayed learning update to NEW challenger checkpoint
```

**The system may automatically formulate a hypothesis, write safe code, train a policy, test it, reject it, collect shadow evidence, and propose an upgrade. It must not autonomously bypass the research budget, alter protected holdouts, rewrite historical results, increase real-money risk or submit physical broker orders.**

## Why this is the next step for this repository

As checked during this planning conversation, GitHub `main` remained `5c7ae82a314b` (2026-11-16), while major work was still on separate PRs including #34, #36–#40. Branch content is **not** automatically mainline capability. In the observed development-branch reports, H005 industry momentum, H003 risk-adjusted momentum and P3 frog-in-the-pan momentum were historical candidates, **not independent prospective cash-return proofs**. Four-Node v1/v2/v3 research found no surviving cost-adjusted Indian-market edge. A reported quote-feed outage left 51 old paper trade exits unresolved. The 2026-11-13 baseline still said no independently evidenced positive forward net alpha. All these findings must be rechecked on the live checkout before claiming a new status.

**Fastest defensible research order:** (1) consolidate and reconcile, (2) fix data/ledger/cost fidelity, (3) self-learning selector for H005/H003/P3 in SHADOW, (4) add offline RL for timing/allocations only if it beats a much simpler policy, (5) Nifty 15-minute forecast-to-option premium paper candidates, (6) autonomous research into new data mechanisms and future evidence.

### In-house boundary and external realities

- **In-house-owned:** strategy DSL, source code, hypothesis engine, features, RL algorithms, risk governor, simulator, OPE, audit, reports, training weights and local deployment. Existing open-source NumPy/Pandas/DuckDB and optional PyTorch are implementation libraries, not paid model/alpha services. A strict zero-third-party-library constraint is **not** necessary for full ownership.
- **External and non-substitutable:** real exchange prices/quotes, event publication, futures/options listings, data-source rights, elapsed forward sessions, broker permission/acceptance and future owner authorization. A synthetic environment is for software tests, **not evidence of real profit**.
- **Safety:** learning services may propose and shadow-trade. Any future capital deployment requires independent human/broker/legal review and a separately designed order process.
- **Research discipline:** automatic experiments cannot evade trial budgets by rephrasing the same idea, rerunning on the same protected holdout or rewriting governance after observing outcomes.

## Operating semantics

- **P0:** before the autonomous learning platform can be called engineering-complete.
- **P1:** required for economically meaningful, scalable multi-desk learning.
- **P2:** experimental advanced research; not a prerequisite for the first valid profitable-edge result.
- **`[ ]`:** proposed or not independently re-audited; not a claim of absence.
- **`IN_HOUSE`:** code/test/fixtures can be completed locally; **`EXTERNAL`:** permissions/data/time/human consent cannot be manufactured.
- **`FORWARD`:** outcomes must occur after policy registration. Replay is historical evidence even when the simulator is sophisticated.
- **No edge found** is an acceptable result. Engineer correctness can be 10/10 even when financial edge is zero.

## Mathematical and operational contracts

### Observations and admissible actions

At time `t`, the learning state is `s_t = (market_features<=available_at_t, current_positions, usable_cash, live_orders, outstanding_risk, market_clock, source_health, uncertainty)`. The raw agent proposes `a_t`; a deterministic independent governor computes:

```text
safe_action_t = RiskGovernor.project(proposed_action_t,
                                    account_state_t,
                                    position_limits_t,
                                    instrument_rules_t,
                                    source_permissions_t)
```

If any safety or data constraint is uncertain, `safe_action_t = NO_TRADE / HOLD / SAFE_EXIT_REQUEST` as allowed by the validated policy. RL cannot relax the governor's limits.

### Economic reward

Let `V_t` be post-cost marked account equity and `F_(t,t+1]` be external deposits/withdrawals in the interval. For equity strictly greater than zero, a defensible cash-flow-adjusted step return is:

```text
r_net,t = (V_(t+1) - V_t - F_(t,t+1]) / V_t
r_excess,t = r_net,t - r_matched_benchmark,t
```

`V` **already includes** realized commissions, fees, spreads, slippage and position values. Do **not** subtract posted costs a second time. If using a custom optimized utility, it must be predeclared and separate from financial net-return reporting. Loss limits, leverage, liquidity and max drawdown are **hard constraints** before optimization. Model predicted rewards are not realized rewards. Missing executable marks remain explicitly `UNKNOWN` or conservatively valued, never optimistically finalized.

### Learning objectives

1. **Edge discovery:** seek `E[r_excess | as-of state] > 0` after real costs and controls, with selection-adjusted uncertainty; no confidence claim from one lucky backtest.
2. **Strategy selection:** select among **validated** candidate arms and CASH given a market context; account for action support and delayed labels.
3. **Sequential policy:** maximize long-horizon constrained portfolio utility including entry/hold/exit and stop/target actions, with accurate next-state economic transitions.
4. **Execution quality:** minimize implementation shortfall for an **already approved** parent intent; do not give an execution agent the right to originate trades.
5. **Meta-learning:** monitor concept drift and retrain challengers, never silently modify the deployed champion.

### Learning-method decision matrix

| Method | First viable use | Typical benefit | Fundamental caveat |
|---|---|---|---|
| Calibrated supervised learner | 15m index move, momentum persistence, cost prediction | Estimates distributions | Forecast skill ≠ options profits |
| Contextual bandit | CASH/H005/H003/P3, trade-vs-abstain, execution timing | Online decisions with simpler feedback | Valid off-policy estimation needs behavior support/propensities |
| Offline behavioral cloning | Strong reproducible imitation baseline | Low variance starting policy | Only mimics existing behavior |
| Conservative offline RL (CQL/IQL-like) | Entry/hold/reduce/exit, bounded positions | Sequential learning without risky physical exploration | Unsupported actions/Q overestimation, simulator misspecification |
| Distributional/constrained RL | Tail-aware position sizing | Optimizes uncertainty-sensitive utility | Hard governor still necessary |
| Model-based RL | Stress and planning in uncertain simulated market | Sample efficiency / scenario analysis | Learner can exploit model errors |
| Online deep RL | Shadow research only unless separately validated | Can adapt to changing contexts | Nonstationarity and feedback delay; can amplify losses |
| Hypothesis-generation AI | Mechanistic candidate proposals, tests, code in sandbox | Accelerates research throughput | More variants amplify false discoveries |

## Acceptance framework for each task

An item is DONE only with: `(1) source code or explicit documented decision, (2) at least one focused automated test, (3) test result, (4) input/source lineage, (5) failure behavior, (6) artifact or commit SHA, (7) owner and status`. `IMPLEMENTED` is not `ECONOMICALLY_VALIDATED`. Report `BLOCKED_EXTERNAL`, `OWNER_ACTION`, `PARTIAL`, `TESTED`, `REJECTED` honestly. Where detailed tests do not apply to a document task, include auditable evidence and a reviewer acceptance record.

## Execution gates and release paths

| Gate | Observable condition | Allowed next step |
|---|---|---|
| G0 Source integrity | Protected research unchanged, one authoritative code path, CI green, legal source rights known | Train on permitted offline data |
| G1 Deterministic market economics | Point-in-time feature/replay parity, fills and P&L reconcile, null-control produces no alpha | Train simple baselines and bandits |
| G2 Learning validity | Causal trajectories, behavior support, reproducible tests, cost robustness | Offline RL challenger training |
| G3 Scientific evaluation | Preregistered tests, corrected selection error, valid confidence, no hidden holdout reuse | Shadow observation |
| G4 Forward validity | Future observed sessions, complete paper ledger, reproducible net P&L, risk within limits | Owner review for paper promotion |
| G5 Economic confirmation | Credible, predeclared positive net alpha after all costs versus relevant benchmarks and uncertainty | `PAPER_CONFIRMED` claim only |
| G6 Real-money eligibility | Broker/legal/feed permissions, owner authorization, separate live OMS and independent controls | Restricted, separately approved future pilot |

**Important:** Gate completion depends on evidence, not on a schedule. No checklist can force a positive market result.

---

# Detailed first-party engineering checklist — SL-00 through SL-34


## SL-00. Canonical repository consolidation and frozen evidence — P0

**Ownership:** `platform/governance`  
**Definition of outcome:** One coherent paper-only codebase, explicit branch ancestry, no retrospective research edits.

- [ ] **SL-00-01** Capture `main` SHA, dependencies, data versions, active scheduled checkout, CI jobs and current owner approvals into an immutable baseline manifest.
- [ ] **SL-00-02** Generate machine-readable dependency graph of PR #34 and PR #36–#40; reconcile stacked branches without replaying overwritten files as if newly validated.
- [ ] **SL-00-03** Diff all existing `forward_edge`, `paper`, `risk`, `execution`, `evaluation`, `forecasting` and `strategies` entry points; choose one authoritative implementation per contract.
- [ ] **SL-00-04** Preserve original protected Q4 2026 trial ledger, registration timestamps, failure outcomes, input hashes and historical read windows byte-for-byte.
- [ ] **SL-00-05** Investigate PR #40 omissions: restore F17 instrument/data gates, F13 renderer, charter/cohort enforcement or document explicit tested replacements.
- [ ] **SL-00-06** Migrate legacy intraday, swing, options, ETF and MF books to a canonical portfolio ledger without inventing fills or changing past marks.
- [ ] **SL-00-07** Reconcile the 51 reported historical positions with missing recorded exits; label `UNRESOLVED` and block promoted P&L unless supported by actual execution evidence.
- [ ] **SL-00-08** Keep Kite/exchange data licensing status as separate permission objects; do not infer paper/synthetic use rights from quote API success.
- [ ] **SL-00-09** Add branch ancestry, source commit and executable entry point to every policy, experiment, quote and simulated trade.
- [ ] **SL-00-10** Separate historical replay, deterministic fixture, local paper, prospectively observed session and independently attested forward evidence in types, not dashboard text.
- [ ] **SL-00-11** Enforce broker isolation at import, runtime, network transport and integration-test layers; no automated policy writes to physical order endpoints.
- [ ] **SL-00-12** Require CI on Windows and Linux: source lint, mypy critical modules, unit tests, integration, property/adversarial tests and code coverage per critical learning service.
- [ ] **SL-00-13** Check main branch protection and mandatory checks through GitHub API; mark OWNER_BLOCKED rather than asserting active protections.
- [ ] **SL-00-14** Write `fb learning audit` planned CLI contract that fails closed on any unmerged dependency, missing test or source permission.
- [ ] **SL-00-15** Create CODEOWNERS-style ownership map for source adapters, simulation, RL, research governance, risk, portfolio, trader UX and accounting.
- [ ] **SL-00-16** Add migration/equivalence report: compare old versus integrated paper decisions on fixed fixture sessions; explain every divergence, including changes to no-trade decisions.
- [ ] **SL-00-17** Pin initial reference strategies H005, H003 and P3 as historical candidates; never label them confirmed prospective edges.
- [ ] **SL-00-18** Produce reproducible signed-or-hashed release evidence bundle without claiming local hashes are independently timestamped.

**Wave acceptance:** No competing canonical OMS or release path; protected research remains identical; no ambiguous live/paper status; automated orders fail closed.


## SL-01. Data contracts, rights, clocks and versioned information availability — P0

**Ownership:** `data/source`  
**Definition of outcome:** Every learner sees only legally usable information available at its actual decision instant.

- [ ] **SL-01-01** Define immutable `RawObservation(source_id, instrument_id, exchange_ts, received_ts, available_at, published_at, revision_id, checksum, entitlement)`.
- [ ] **SL-01-02** Create effective-dated instrument universe for NSE/BSE equities, listed ETFs, index options, futures and MF schemes; preserve historical delistings and contract changes.
- [ ] **SL-01-03** Define event-time/availability-time dual indexing; do not use a close, filing, NAV or revised report before it was observable.
- [ ] **SL-01-04** Implement provider-specific timezone/holiday/half-day/session/circuit/expiry/cutoff calendars; never infer session endings from a constant alone.
- [ ] **SL-01-05** Add immutable source snapshots with allowed retention, redistribution status and non-display/licensing provenance for each data class.
- [ ] **SL-01-06** Create explicit `USE_ALLOWED`, `UNKNOWN_RIGHTS`, `DENIED` entitlement states and fail closed on unknown permissions for operational market feed consumers.
- [ ] **SL-01-07** Develop historical availability ledger for price corrections, dividends, splits, symbol/ISIN changes, index constituent changes and futures roll schedules.
- [ ] **SL-01-08** Build point-in-time index membership snapshots and survivorship-safe historical corporate action joins for feature training.
- [ ] **SL-01-09** Add option strike, side, underlying, lot, expiry and exact token mappings with effective dates; reject reconstructed fictional listed contracts.
- [ ] **SL-01-10** Archive quote bid/ask, depth, OI and last trade separately with timestamp/age and entitlement; never make index level a tradable option price.
- [ ] **SL-01-11** Distinguish exchange tick vs broker receipt and clock-skew uncertainty; derive `decision_available_at=max(all input availability times)`.
- [ ] **SL-01-12** Implement retriable, idempotent ingesters with watermarks, atomic batches, source correction events and data-quality dashboards.
- [ ] **SL-01-13** Build negative test: introduce future publication revisions and assert no prior features, signals or policy rankings change.
- [ ] **SL-01-14** Add controlled redaction/deletion and retention periods for exchange-licensed feeds; delete prohibited data without corrupting historical provenance.
- [ ] **SL-01-15** Declare market data ingestion unavailable during outages, holidays or unauthorized periods; expose `NO_DATA` and stop policy decisions.
- [ ] **SL-01-16** Provide training/validation/test data manifests with as-of cutoffs, raw file hashes, transforms and explicit unavailable source periods.
- [ ] **SL-01-17** Prevent normalization, universe filtering and feature selection from fitting on validation/test periods.
- [ ] **SL-01-18** Block paper simulation using broker live data where provider terms prohibit it until written interpretation or a permitted source is obtained.

**Wave acceptance:** Time-travel, revision, delisting and entitlement probes cannot change historical decisions or produce unauthorized executable evaluations.


## SL-02. Market feature store, event stream and causal representations — P0

**Ownership:** `features/core`  
**Definition of outcome:** Materialize causal, testable, stable features for supervised, bandit and RL learners.

- [ ] **SL-02-01** Create canonical `FeatureDefinition` with formula hash, window, market, horizon, input lineage, lag, leakage tests and data type.
- [ ] **SL-02-02** Implement streaming OHLCV aggregation for 1m/5m/15m/30m/1h/day with missing-minute flags and completed-bar semantics.
- [ ] **SL-02-03** Add price-return, volatility, range, trend, volume participation, gap, session VWAP, breadth and liquidity as separate causal feature families.
- [ ] **SL-02-04** Add options IV surface, skew, term structure, Greeks and OI features only where observed quotes/contracts support estimation.
- [ ] **SL-02-05** Construct cross-index breadth, constituent concentration, Nifty/BankNifty co-movement and sector relative-strength features with aligned timestamps.
- [ ] **SL-02-06** Extract event-availability indicators for NSE/BSE filings, corporate announcements, results, shareholding and financial statement revisions.
- [ ] **SL-02-07** Implement cross-sectional sector, size, liquidity, beta and corporate-action neutralization fitted on past samples only.
- [ ] **SL-02-08** Keep raw, adjusted and total-return price series separated; enforce dividend and split correctness by arithmetic reconciliation.
- [ ] **SL-02-09** Add `FeatureSnapshot` with observation/availability timestamp, feature version, source hashes and missingness masks.
- [ ] **SL-02-10** Guard against stale indicator values, forward-filled expired contracts and all market-close-to-open boundary leakage.
- [ ] **SL-02-11** Create streaming/offline parity fixtures: same market event prefix must produce same feature values in replay and forward processing.
- [ ] **SL-02-12** Apply schema migrations with checksummed transformations and automatic feature lineage invalidation when raw source corrections arrive.
- [ ] **SL-02-13** Measure feature stability by date, instrument, regime and provider; surface feature drift before automatic retraining.
- [ ] **SL-02-14** Detect proxy leakage (index weights inferred from revised constituents, event timestamps after publication, synthetic corporate-action adjustment).
- [ ] **SL-02-15** Store exclusion reasons and instrument eligibility at each timestamp so the learner can model genuine no-trade opportunities.
- [ ] **SL-02-16** Make feature computation idempotent and deterministic under out-of-order, duplicate and corrected input events.
- [ ] **SL-02-17** Benchmark feature inference latency and memory on realistic multi-index and liquid-stock universes.
- [ ] **SL-02-18** Force each learned policy to emit all feature IDs and their availability proof in its decision manifest.

**Wave acceptance:** Future mutation changes no earlier feature; replay and streaming agree bit-for-bit or within predeclared floating tolerances.


## SL-03. Self-learning system contracts and agent role boundaries — P0

**Ownership:** `learning/core`  
**Definition of outcome:** Define precisely what can learn, what can propose, and what never gets delegated to an RL policy.

- [ ] **SL-03-01** Implement `ResearchAgent`, `HypothesisGenerator`, `ExperimentExecutor`, `Evaluator`, `PolicyRegistry`, `RiskGovernor`, `PaperOperator`, `Auditor` interfaces.
- [ ] **SL-03-02** Use one event-sourced learning state machine: `HYPOTHESIS → REGISTERED → TRAINED → OOS_TESTED → SHADOW → FORWARD_CANDIDATE → REVIEWED → RETIRED`.
- [ ] **SL-03-03** Keep immutable trial identity over family, instrument universe, horizon, dataset version, feature set, action space, reward, code SHA and cost model.
- [ ] **SL-03-04** Separate learning-state writes from portfolio-state writes; research agents must not change cash, fills, ledger balances or release policy.
- [ ] **SL-03-05** Model all strategy recommendations as `PROPOSE`, `WATCH`, `NO_TRADE`, `EXIT_PROPOSAL`; ownership gates decide eligibility.
- [ ] **SL-03-06** Create reason-coded rejection paths for insufficient sample, leakage, stale data, missing rights, excessive cost and unsafe action.
- [ ] **SL-03-07** Expose `PolicyDecision` with probabilities, expected net excess, uncertainty, capacity, risk budget, provenance and available-at clock.
- [ ] **SL-03-08** Add freeze windows: retraining cannot mutate currently deployed policy weights, state definitions or reward functions during a session.
- [ ] **SL-03-09** Declare learning budgets: CPU/GPU/time/disk/trial counts; budget exhaustion must stop research automatically.
- [ ] **SL-03-10** Build human-only permission checks for changing statistical thresholds, capital caps, strategy families, broker flags or protected holdouts.
- [ ] **SL-03-11** Create structured machine-readable experiment reports and human-readable economic rationale, with no success claims from test count.
- [ ] **SL-03-12** Implement data/reward/protocol contracts as schemas with Pydantic or pure typed validators, pinned by hashes.
- [ ] **SL-03-13** Declare deterministic simulation mode and probabilistic training mode with seed registers and reproducibility reports.
- [ ] **SL-03-14** Integrate with existing `forward_edge/` claim tiers and ADRs via adapters; no parallel, less strict promotion path.
- [ ] **SL-03-15** Implement familywise lineage: descendants share parent discovery-trial budget and cannot reset failure history by renaming.
- [ ] **SL-03-16** Include policy abstention and market-close actions in every decision API; forbid forced trades.
- [ ] **SL-03-17** Develop kill-switch and audited manual intervention paths that work even if all learning services are unavailable.
- [ ] **SL-03-18** Define outcome statuses `FAIL`, `NO_EDGE`, `CANDIDATE`, `INSUFFICIENT_EVIDENCE`, `PAPER_CONFIRMED`, never fabricate evidence.

**Wave acceptance:** A simulated hostile agent cannot alter release gates, change the ledger, falsify timestamps or place a physical order.


## SL-04. Finite-capital market simulator and event-driven POMDP — P0

**Ownership:** `learning/env`  
**Definition of outcome:** Train policies only against explicit, falsifiable assumptions about market observability and execution.

- [ ] **SL-04-01** Model the task as a partially observable sequential decision process, not a fully known future price trajectory.
- [ ] **SL-04-02** Define state `s_t = (point-in-time market features, holdings, cash, drawdown, orders, remaining time, data health, model uncertainty)`.
- [ ] **SL-04-03** Define policy action as abstract desired position/option intent; the risk governor projects it into a feasible trade or NO_TRADE.
- [ ] **SL-04-04** Create configurable episode boundaries by instrument/session/month/expiry; no accidental carry across account restarts.
- [ ] **SL-04-05** Add event-driven transitions for quote, bar close, trade, fill, cancel, rejection, halt, corporate action, cash flow and model update.
- [ ] **SL-04-06** Make market events immutable and independent of policy action except explicitly modeled impact and marketable participation.
- [ ] **SL-04-07** Enforce cash availability, whole-share/lot sizing, exchange limits, options expiry, settlement and instrument eligibility on every step.
- [ ] **SL-04-08** Support canonical order states `NEW/ACK/PARTIAL/FILLED/CANCELLED/REJECTED/UNKNOWN` with durable write-ahead transitions.
- [ ] **SL-04-09** Implement reward event emission at actual valuation events rather than hindsight labeling at entry.
- [ ] **SL-04-10** For overlapping SL and TP on the same OHLC candle, choose adverse-first or unresolved; require tick/depth to resolve ordering.
- [ ] **SL-04-11** Model bid-ask spread, queue probability, depth depletion, adverse selection, partial fills, stale data and varying latency.
- [ ] **SL-04-12** Model session-open gap-through-stop and exchange halt as non-guaranteed stop execution; preserve open risk when impossible to close.
- [ ] **SL-04-13** Parameterize exchange fee/tax/impact/participation rules point in time with actual instrument category.
- [ ] **SL-04-14** Create historical replay simulator, synthetic stress simulator and prospectively observed paper simulator with different evidence labels.
- [ ] **SL-04-15** Allow replay snapshots/rewind of the simulator while preventing the learner from inspecting future scenario random seeds.
- [ ] **SL-04-16** Implement deterministic no-trade baseline and fixed-rule policy baselines in exactly the same simulator.
- [ ] **SL-04-17** Model stochastic execution uncertainty as scenario distribution; do not equate simulated fills to brokerage executions.
- [ ] **SL-04-18** Build differential tests against canonical OMS and ledger at every portfolio transition.
- [ ] **SL-04-19** Add impossibility assertions: negative cash without approved margin, fictitious contracts, zero-cost instant fills, fill before quote.
- [ ] **SL-04-20** Publish simulator fidelity manifest showing which assumptions are observed, calibrated, conservative or wholly synthetic.

**Wave acceptance:** An RL agent cannot create money by churning, crossing stale quotes, moving its stop after the fact or exploiting the simulator clock.


## SL-05. Economic reward, capital accounting and anti-reward-hacking — P0

**Ownership:** `learning/rewards`  
**Definition of outcome:** Reward only real after-cost portfolio economics, with risk treated as hard constraints.

- [ ] **SL-05-01** Define step net return from post-cost marked equity, excluding deposits/withdrawals and marking open positions at executable exit-side quotes when possible.
- [ ] **SL-05-02** Compute reward using posted ledger costs exactly once: brokerage, STT/CTT if relevant, exchange fees, GST, stamp duty, spread, slippage and measured impact.
- [ ] **SL-05-03** Use matched benchmark excess as a separately reported objective; never quietly subtract both benchmark drift and risk penalties twice.
- [ ] **SL-05-04** Distinguish gross return, net return, net alpha, downside utility, risk constraint violation and estimation confidence in separate metrics.
- [ ] **SL-05-05** Define reward as cumulative portfolio utility rather than average winning trade, win rate, gross P&L or raw price direction.
- [ ] **SL-05-06** Use drawdown, CVaR, gross/net exposure, turnover, concentration, margin and liquidity as hard policy constraints before reward optimization.
- [ ] **SL-05-07** Add risk penalties to diagnostic objective with frozen coefficients; avoid replacing a failed hard constraint with small negative reward.
- [ ] **SL-05-08** Include flat/cash and no-trade periods in equity-curve and Sharpe calculations.
- [ ] **SL-05-09** Enforce end-of-episode liquidation using feasible exit-side prices, unsettled states and explicit unknown marks.
- [ ] **SL-05-10** Penalize unnecessary churn through realized costs, not an arbitrary fee that might be tuned to hide losses.
- [ ] **SL-05-11** Handle option premium, theta/IV, exercise/assignment/expiry and defined-risk spread value in position-based P&L.
- [ ] **SL-05-12** Handle futures variation margin, margin requirement and liquidation stress in the simulator, separately from cash-equity bookkeeping.
- [ ] **SL-05-13** Handle ETF distributions and MF NAV/exit loads/settlement with asset-specific valuation clocks.
- [ ] **SL-05-14** Test zero-price-change high-turnover policies produce negative net P&L under positive costs.
- [ ] **SL-05-15** Test rewards cannot be increased by fake wins, reopening after crash, postponing loss realization or ignoring open adverse marks.
- [ ] **SL-05-16** Create per-action reward decomposition and per-episode double-entry reconciliation with one source of truth.
- [ ] **SL-05-17** Add reward misspecification audit: verify that a higher reward ranking implies better actual defined economic objective on holdout.
- [ ] **SL-05-18** Fix reward and risk coefficient versions before each registered experiment; any tuning is a new trial.

**Wave acceptance:** All reward components reconcile to cash/positions; adversarial reward-hacking agents cannot outperform by exploiting accounting artifacts.


## SL-06. Backtest economics, market impact and policy capacity — P0

**Ownership:** `learning/economics`  
**Definition of outcome:** Quantify why an edge disappears when spread, timing, size, fees or volatility changes.

- [ ] **SL-06-01** Implement current and historical Indian charge schedules as effective-dated configuration with source and date-of-validity tests.
- [ ] **SL-06-02** Model intended notional, max participation, per-contract minimum costs and execution delay separately for every asset class.
- [ ] **SL-06-03** Calculate execution shortfall relative to the quote legally observed at the decision moment, not to a future midprice.
- [ ] **SL-06-04** Build option spread/depth curve and quote staleness filters for exact contract, strike and expiry.
- [ ] **SL-06-05** Estimate impact and adverse selection from observed data where available; expose unverifiable estimates as uncertainty intervals.
- [ ] **SL-06-06** Use max(optimistic, base, stressed) cost suite in evaluation, with profitability gate requiring defensible robustness.
- [ ] **SL-06-07** Apply whole-share rounding and fixed charges to small-account H005/H003/P3 books; test INR 50k, 2L, 10L sensitivity as scenarios, not promises.
- [ ] **SL-06-08** Implement cash drag, dividends and idle capital explicitly in monthly equity-book economics.
- [ ] **SL-06-09** Simulate rebalance delays and liquidity caps while maintaining unchanged decision-time ranking and point-in-time universe.
- [ ] **SL-06-10** Model price-band, lower/upper circuit, expiry day and market-halt cases as rejected or unresolved orders.
- [ ] **SL-06-11** Build transaction-cost attribution by brokerage, STT, spread, impact, slippage, exchange fees and market movement.
- [ ] **SL-06-12** Construct capacity curves net return versus deployed capital and market participation, with confidence bands.
- [ ] **SL-06-13** Add trade scheduling and TWAP/VWAP comparison without assuming one is universally superior.
- [ ] **SL-06-14** Compare dynamic execution policies to marketable-limit baseline under same arrival time and quote observations.
- [ ] **SL-06-15** Flag strategy whose positive alpha is concentrated in contracts with unknown depth or sample-size-limited liquidity.
- [ ] **SL-06-16** Cross-check conservative OHLC fills against tick replay when available; label discrepancy and propagate to uncertainty.
- [ ] **SL-06-17** Exhaustively test missing bid/ask, zero depth, stale LTP, spread expansion and illiquid ETF cases.
- [ ] **SL-06-18** Require final P&L reproducibility from the raw economic ledger without consulting model-generated text.

**Wave acceptance:** No candidate passes profitability gates if positive net return exists only under midpoint fills, unrealistic capacity or omitted fixed fees.


## SL-07. Training trajectories, behavior policies and action support — P0

**Ownership:** `learning/datasets`  
**Definition of outcome:** Create legitimate RL datasets with what an agent saw and what actually occurred.

- [ ] **SL-07-01** Define episode key `(dataset_version, instrument, session_id, strategy_id, behavior_policy_version, account_id)` with immutable clock.
- [ ] **SL-07-02** Store full `(s_t, eligible_action_mask, chosen_action, action_probability, fill, reward, s_{t+1}, done, provenance)` rows.
- [ ] **SL-07-03** When behavior propensities are unknown, label `PROPENSITY_UNAVAILABLE`; do not create fabricated numbers.
- [ ] **SL-07-04** Distinguish replayed hypothetical counterfactual rewards from observed actions and outcomes using separate schemas.
- [ ] **SL-07-05** Capture rejected signal, no-trade and abstention opportunities, not only executed winners.
- [ ] **SL-07-06** Prevent reconstructing missing live actions as if a previous policy chose them prospectively.
- [ ] **SL-07-07** Encode action support set and coverage density by regime/instrument/size; mark unsupported actions as OOD.
- [ ] **SL-07-08** Discard data points where unresolved fills, insufficient quote history or legal data retention prevent valid reward.
- [ ] **SL-07-09** Build contiguous training trajectories without session/expiry leakage; allow overnight positions only when strategy permits.
- [ ] **SL-07-10** Use explicit `action_decision_ts` and `first_eligible_fill_ts`; refuse same-bar perfect entry at future close.
- [ ] **SL-07-11** Create behavior-cloning baseline and action-frequency/entropy diagnostic for every historical strategy.
- [ ] **SL-07-12** Partition trajectories by chrono fold, correlated index/session group and data source; embargo overlapping holdings.
- [ ] **SL-07-13** Store min/max observed position size, leverage, spread, holding time and action support per behavior policy.
- [ ] **SL-07-14** Check bias from logging only placed trades; estimate policy support and refuse invalid off-policy comparisons.
- [ ] **SL-07-15** Implement deterministic trajectory builder with hash-for-hash reproducibility across OS and CPU architectures.
- [ ] **SL-07-16** Build test corruptions: swapped rewards, future fields, missing actions, clock drift, duplicate quotes, missing propensities.
- [ ] **SL-07-17** Provide parquet snapshots plus schema and audit manifest; no network use required to replay permitted recorded fixtures.
- [ ] **SL-07-18** Document precisely which behavioral datasets are adequate for bandits, supervised predictors, offline RL or none.

**Wave acceptance:** Every OPE estimator knows its support and propensity assumptions; unseen actions cannot gain an invented positive reward.


## SL-08. Research hypothesis DSL, causal mechanisms and strategy grammar — P0

**Ownership:** `learning/discovery`  
**Definition of outcome:** Create interpretable, testable strategy candidates without blind unrestricted program search.

- [ ] **SL-08-01** Build typed strategy grammar with instrument scope, event trigger, lookback, feature transform, forecast horizon, entry, exit and allowed order action.
- [ ] **SL-08-02** Assign every feature and candidate to an economic hypothesis: risk premium, liquidity, behavioral delay, structural demand, event timing or market microstructure.
- [ ] **SL-08-03** Require a mechanism statement, predicted direction, horizon, failure regime and falsifying test before candidate construction.
- [ ] **SL-08-04** Create AST-based program representation and safe interpreter with bounded expression size, no arbitrary Python, no network and no data mutation.
- [ ] **SL-08-05** Expose allowed primitives: rolling rank, decile, lag, volatility, relative-strength, sector neutralization, event age and cross-sectional group.
- [ ] **SL-08-06** Add declarative constraints to prohibit future references, invalid implicit joins, lookahead pivots and calendar offsets beyond available data.
- [ ] **SL-08-07** Implement algebraic and semantic canonicalization so duplicate strategies share trial identity even if expressions differ syntactically.
- [ ] **SL-08-08** Add family similarity clustering by input features, behavior, trades, sector and realized P&L; do not count cloned alphas as independent.
- [ ] **SL-08-09** Create static cost upper-bound estimator and minimum tradable-horizon check to reject impossible high-turnover concepts early.
- [ ] **SL-08-10** Generate a baseline/null hypothesis paired with every new candidate before any return series is computed.
- [ ] **SL-08-11** Define event hypotheses against publication timestamp, not fiscal quarter label or later revised filing.
- [ ] **SL-08-12** Define long, short, cash, hedge and options hypotheses with market authorization and paper-specific masks.
- [ ] **SL-08-13** Implement crossover/noise/metamorphic tests: future mutation, time reversal, shuffled labels, lagged features and random signs.
- [ ] **SL-08-14** Add structural complexity cap, logic-depth cap and portfolio turnover budget; disallow unconstrained genetic-code explosion.
- [ ] **SL-08-15** Store raw researcher prompt, generated AST, parent IDs, generated code SHA, economic rationale and cost estimate in append-only manifests.
- [ ] **SL-08-16** Detect invalid redundant filters (combining highly correlated momentum transforms) and report effective number of economic ideas.
- [ ] **SL-08-17** Implement machine-checkable pre-registration from DSL spec before backtesting any protected candidate.
- [ ] **SL-08-18** Add hypothesis audit that returns `NOT_TESTABLE` when required market data or natural experiment does not exist.

**Wave acceptance:** A candidate cannot evaluate on protected holdouts without a frozen registration; duplicate rephrasing does not reset trial budget.


## SL-09. Automated alpha discovery, novelty and counterfactual research — P1

**Ownership:** `learning/researcher`  
**Definition of outcome:** Search useful economic hypotheses efficiently while preserving a scientifically valid selection process.

- [ ] **SL-09-01** Create `IdeaProposal` with mechanism, anticipated return source, feature availability, estimated capacity, relevant desk and proposed falsification.
- [ ] **SL-09-02** Use self-generated candidates and stored literature notes strictly as proposals, never as evidence that a return exists.
- [ ] **SL-09-03** Implement acquisition priority from economic plausibility, available sample, execution costs, novelty and expected information gain.
- [ ] **SL-09-04** Cap candidate generation per family and prevent agent from silently increasing search budgets after failed outcomes.
- [ ] **SL-09-05** Use diverse source families: price/volume, filings, financial fundamentals, momentum, liquidity, options microstructure, ETF premiums and corporate actions.
- [ ] **SL-09-06** Separate new information sources from new transforms of identical candle patterns; record whether marginal information actually exists.
- [ ] **SL-09-07** Create candidate comparison against matched random-book, time-shifted signal, sector-neutral and equal-turnover controls.
- [ ] **SL-09-08** Use conditional permutation and targeted ablation to establish which source truly drives any measured gain.
- [ ] **SL-09-09** Create candidate-level alternative explanations and a checklist for corporate-action/adjusted-price artifacts.
- [ ] **SL-09-10** Measure marginal improvement given existing accepted exposures rather than judging only stand-alone Sharpe.
- [ ] **SL-09-11** Detect portfolio alpha duplication via turnover-adjusted correlations and common market/sector factor exposures.
- [ ] **SL-09-12** Compute economic break-even spread or fee for each candidate; reject if required execution advantage is unattainable.
- [ ] **SL-09-13** Develop isolated sandbox for experiment construction and test generation; deny access to validation/test labels until preregistration.
- [ ] **SL-09-14** Automate first-line test reviewer independent of candidate generator; reviewer must attempt to falsify and document defects.
- [ ] **SL-09-15** Build failure memory: store why 128 Four-Node variants and other intraday ideas failed; penalize exact repeats, not novel mechanisms.
- [ ] **SL-09-16** Provide transparent leaderboard showing proposed, rejected without trial, trained, inconclusive and provisionally positive counts.
- [ ] **SL-09-17** Use predeclared holdout sample budgets and cryptographic seals, preserving which candidates have already viewed which periods.
- [ ] **SL-09-18** Output explicit `NO_NEW_EDGE_FOUND` if all candidates fail, with no artificial winner selection.

**Wave acceptance:** Search process records every candidate and trial, rejects duplicates and never promotes a best-of-many result without selection correction.


## SL-10. Baseline learning systems before deep reinforcement learning — P0

**Ownership:** `learning/baselines`  
**Definition of outcome:** Prove that new complexity delivers economic value over simple trading and forecasting baselines.

- [ ] **SL-10-01** Implement constant CASH, broad-market invest-and-hold, matched momentum, volatility-scaled buy/hold and existing fixed-rule paper baselines.
- [ ] **SL-10-02** Implement linear/ridge/logistic predictors with strictly chronological training and comparably normalized inputs.
- [ ] **SL-10-03** Implement tree-based supervised model where in-house or existing standard libraries permit reproducible controlled tuning.
- [ ] **SL-10-04** Implement probability calibration with walk-forward isotonic/Platt/temperature transforms fitted only on prior validation windows.
- [ ] **SL-10-05** Use distributional random-walk, drift and stationary block bootstrap nulls already represented in `forecasting/null.py`.
- [ ] **SL-10-06** Compare baseline alpha by asset class, horizon, regime, transaction cost, exposure and signal availability.
- [ ] **SL-10-07** Expose confidence bands, forecast calibration and net P&L without conflating predictive skill with tradability.
- [ ] **SL-10-08** Make a thresholded forecast-to-trade baseline with cash and all risk gates before RL can claim incremental value.
- [ ] **SL-10-09** Implement deterministic buy/hold/sell threshold policy plus fixed stop/target/time-stop for exact apples-to-apples baseline.
- [ ] **SL-10-10** Implement fitted Q evaluation for finite discrete action and low-dimensional baseline to test sequential edge feasibility.
- [ ] **SL-10-11** Assess achievable advantage under a hypothetical zero-cost upper bound and real base cost; classify marginal patterns cost-dominated.
- [ ] **SL-10-12** Test all baselines on the same episodes, same available information, same portfolio size and same policies for unresolved fills.
- [ ] **SL-10-13** Prohibit comparing one algorithm trained on 2016–2025 to an inferior baseline trained on older or less complete periods.
- [ ] **SL-10-14** Log annualized turnover, cost drag, max drawdown, downside tail risk, factor exposures and fraction of time invested.
- [ ] **SL-10-15** Create baseline registry and immutable champion label independent of any new RL training metrics.
- [ ] **SL-10-16** Gate entry to deep RL research on demonstrated supervised or simple policy evidence plus realistic sample size.

**Wave acceptance:** An RL candidate cannot move to forward testing merely by beating a deliberately weak cash or random predictor baseline.


## SL-11. Contextual bandits for strategy choice and abstention — P0

**Ownership:** `learning/bandits`  
**Definition of outcome:** Start self-learning with the simplest policy class that matches independent per-decision choices.

- [ ] **SL-11-01** Define bandit context as predecision regime, forecast, strategy quality, liquidity, costs, portfolio and data-health state.
- [ ] **SL-11-02** Define arm set `CASH`, `H005`, `H003`, `P3` plus externally eligible desks only; never force available arms to trade.
- [ ] **SL-11-03** Implement epsilon-greedy only for offline benchmark, with actual production/shadow exploration constrained to non-cash observation.
- [ ] **SL-11-04** Implement UCB/linear-UCB and Thompson-style contextual selection with uncertainty and capped action probabilities.
- [ ] **SL-11-05** Calibrate inverse-propensity and doubly robust estimators only when action probability and support assumptions hold.
- [ ] **SL-11-06** Distinguish delayed outcomes: monthly-book reward not available on the next 15-minute bar.
- [ ] **SL-11-07** Implement delayed-feedback queues, unique outcome settlement IDs and score updates only after the true evaluation horizon.
- [ ] **SL-11-08** Handle missing, censored and invalid observations by pending/unknown status, never zero reward substitution.
- [ ] **SL-11-09** Implement `select_arm` with risk governor projection; log whether the proposed action was blocked and why.
- [ ] **SL-11-10** Implement deterministic replay plus frozen policy shadow evaluation against equal-weight and best past arm baselines.
- [ ] **SL-11-11** Enforce concentration and correlated arm limits; selecting three momentum strategies cannot triple the permitted market bet.
- [ ] **SL-11-12** Use confidence-based abstention when top arms have overlapping estimated performance intervals.
- [ ] **SL-11-13** Compare regime-conditioned vs global selection with dependence-aware statistical uncertainty and predeclared trial budget.
- [ ] **SL-11-14** Test cold start and regime changes; initialize untested arms as unproven rather than artificially optimistic.
- [ ] **SL-11-15** Use observation-only shadow randomization where legal and useful; do not create live financial losses for exploration.
- [ ] **SL-11-16** Build property test: missing action support prevents invalid OPE and no model gets extra capital for missing data.
- [ ] **SL-11-17** Introduce rolling evidence decay only after predeclared calibration; prevent hindsight tuning of recency windows.
- [ ] **SL-11-18** Attribute any performance uplift to selection timing versus changing factor exposures or differential costs.

**Wave acceptance:** Bandit engine is able to learn in SHADOW, abstain, and report valid uncertainty without access to physical orders.


## SL-12. Offline RL foundations, behavior cloning and policy support — P0

**Ownership:** `learning/offline_rl`  
**Definition of outcome:** Learn sequential policies only from auditable behavior data and realistic counterfactual limitations.

- [ ] **SL-12-01** Implement behavior cloning MLP and simple linear imitation baseline on strictly logged feasible actions.
- [ ] **SL-12-02** Implement discrete `HOLD/ENTER/REDUCE/EXIT/CASH` environment before continuous position sizing.
- [ ] **SL-12-03** Create BC validation metrics: action agreement, calibration, state coverage, policy-induced turnover and economic performance.
- [ ] **SL-12-04** Implement state-action occupancy and k-nearest-neighbor or density support estimator to detect OOD chosen actions.
- [ ] **SL-12-05** Block offline Q-value inference for unseen option contracts, extreme leverage or unobserved quote regimes.
- [ ] **SL-12-06** Implement deterministic fitted Q iteration and fitted Q evaluation on small tabular/linear policy problems before neural versions.
- [ ] **SL-12-07** Validate Bellman backup and terminal state math on hand-derived two-step and three-step markets.
- [ ] **SL-12-08** Implement safe fallback to behavior policy or CASH when uncertainty or support tests fail.
- [ ] **SL-12-09** Compare offline learned policy with behavior cloning and old behavior, not with an irrelevant random-action agent.
- [ ] **SL-12-10** Keep deterministic evaluation on logged actions separate from simulator-based counterfactual rollouts.
- [ ] **SL-12-11** Use chronological replay buffers and contiguous sequences; no uniform mixing that makes training test regimes indistinguishable.
- [ ] **SL-12-12** Split trajectories by decision time and correlated session/account event group; embargo overlapping forward rewards.
- [ ] **SL-12-13** Track conservative policy improvement by entire monthly/session portfolio P&L, not only training loss convergence.
- [ ] **SL-12-14** Add propensity-sensitive evaluation only for data with legitimate behavior policy logging.
- [ ] **SL-12-15** Implement target-network/replay math with numerically stable losses and checkpoint loading tests.
- [ ] **SL-12-16** Implement random-label, random-policy and zero-edge market fixtures; any fabricated improvement fails algorithm admission.
- [ ] **SL-12-17** Keep offline RL policy in research/shadow until approved by independent OOS and forward economic gates.
- [ ] **SL-12-18** Document failure: offline observation data alone cannot determine outcomes of arbitrary new trading actions with fidelity.

**Wave acceptance:** No offline agent can deploy unsupported actions or claim tested policy improvement without valid environment/OPE assumptions.


## SL-13. Conservative offline RL: CQL, IQL and constrained alternatives — P1

**Ownership:** `learning/offline_rl`  
**Definition of outcome:** Build locally owned robust RL alternatives and compare them as predeclared algorithms, not unlimited hyperparameter searches.

- [ ] **SL-13-01** Implement CQL-style penalization for value estimates on actions outside logged behavior support; verify Q ranking on toy datasets.
- [ ] **SL-13-02** Implement IQL-style expectile state-value regression, in-dataset Bellman targets and advantage-weighted behavior policy extraction.
- [ ] **SL-13-03** Implement TD3+BC or BCQ-style action constraints for continuous allocations only if behavior data and compute justify them.
- [ ] **SL-13-04** Implement separate value, policy, target and support modules with typed gradients, optimizer state and deterministic seeds.
- [ ] **SL-13-05** Use n-step and temporal-difference targets that honor the original session/contract termination, fees and outstanding positions.
- [ ] **SL-13-06** Add uncertainty ensemble or bootstrap interval around Q estimates and change capital eligibility based on conservative lower bounds.
- [ ] **SL-13-07** Use action masks for tradable instruments, remaining time, max loss, concentration and permitted order types.
- [ ] **SL-13-08** Compare CQL/IQL against BC, fitted Q, simpler contextual bandit and deterministic trading baseline on identical samples.
- [ ] **SL-13-09** Tune hyperparameters using train/validation only; lock number of configurations and record every failed run.
- [ ] **SL-13-10** Check sensitivity to support penalty, expectile, discount factor, reward scale, action discretization and transaction costs.
- [ ] **SL-13-11** Include at least one zero-alpha random-walk and one known-alpha seeded toy environment as positive and negative controls.
- [ ] **SL-13-12** Use independent environment seeds and trading periods; never select on a single historical market crash or bull market.
- [ ] **SL-13-13** Compute performance by volatility regime, market direction, sector, liquidity and capital bucket.
- [ ] **SL-13-14** Implement policy collapse detection (constant action, excessive leverage, excessive trading, silent liquidation).
- [ ] **SL-13-15** Treat training improvement as optimization progress only, not a claim of real market performance.
- [ ] **SL-13-16** Archive all weights, data lineage, optimizer states, model cards and audit findings for every trial.
- [ ] **SL-13-17** Implement conservative rejection: if uncertain improvement does not exceed cost and evaluation error, prefer current champion/CASH.
- [ ] **SL-13-18** Keep algorithm source implementation in-house; optional PyTorch is infrastructure, not an outsourced signal model.

**Wave acceptance:** One independently predeclared conservative RL family beats the correct baseline on frozen OOS after costs or remains unpromoted.


## SL-14. Risk-sensitive and distributional reinforcement learning — P1

**Ownership:** `learning/risk_rl`  
**Definition of outcome:** Optimize lower-tail portfolio performance under hard safety constraints, not greedy headline returns.

- [ ] **SL-14-01** Define distributional return targets for quantiles, downside and catastrophic tail episodes with realistic financial paths.
- [ ] **SL-14-02** Implement quantile-regression critic or categorical return distribution with numerical checks and bound clipping.
- [ ] **SL-14-03** Implement CVaR and expected-shortfall estimates over actual portfolio net-return episodes, with finite sample uncertainty.
- [ ] **SL-14-04** Use constrained MDP Lagrangian as research objective but keep risk governor as independent hard safety layer.
- [ ] **SL-14-05** Add time-varying drawdown state, loss-streak stress and remaining risk budget to policy input.
- [ ] **SL-14-06** Implement terminal ruin/forced liquidation states; never hide them through early episode termination.
- [ ] **SL-14-07** Study uncertainty-aware policy abstention and fractional sizing under bootstrap and stress intervals.
- [ ] **SL-14-08** Compare mean-return, mean-variance, CVaR and downside-utility policies on exactly matched data and cost assumptions.
- [ ] **SL-14-09** Forbid risk limit changes during model training unless separately approved and hashed.
- [ ] **SL-14-10** Add crash/reversal/volatility expansion and margin spike scenario sets, generated from historical blocks plus transparent synthetic perturbations.
- [ ] **SL-14-11** Model position size scaling under fat-tailed costs, market impact and correlated book drawdowns.
- [ ] **SL-14-12** Use proper baselines: risk-budgeted cash/momentum, volatility-targeted strategy, robust mean-variance allocation.
- [ ] **SL-14-13** Stress risk model errors, missing observations and unexpected position correlation; fail closed on budget breach.
- [ ] **SL-14-14** Require expected tail loss estimates and their uncertainty in every approved policy decision manifest.
- [ ] **SL-14-15** Test that increasing estimated risk without increasing expected value cannot increase allocated size.
- [ ] **SL-14-16** Publish long-horizon rolling max drawdown, time-underwater, CVaR, cash drag and ruin probability in reports.
- [ ] **SL-14-17** Reject agents that earn small frequent wins but occasionally incur catastrophic loss beyond stated budget.
- [ ] **SL-14-18** Develop reversibility: risk-off and rollback must work without model state, internet connectivity or GPU.

**Wave acceptance:** No reward improvement can override hard loss, capital, liquidity or concentration limits; test crashes force abstention or de-risking.


## SL-15. Model-based RL, market world models and falsification — P2

**Ownership:** `learning/world_model`  
**Definition of outcome:** Use world models as uncertain scenario generators, never as a source of guaranteed market returns.

- [ ] **SL-15-01** Create probabilistic transition model for observable returns, liquidity, spreads, volatility and missingness conditional on regime.
- [ ] **SL-15-02** Build stationary/block-bootstrap, AR, volatility and resampled-residual world-model nulls before neural sequence models.
- [ ] **SL-15-03** Implement probabilistic temporal encoder with past-only attention and explicit per-horizon uncertainty.
- [ ] **SL-15-04** Keep agent impact models separate from exogenous market dynamics and calibrate only at observed participation levels.
- [ ] **SL-15-05** Generate future scenario paths consistent with positivity, market hours, corporate actions and observed contract lifetimes.
- [ ] **SL-15-06** Apply proper scoring rules (CRPS, pinball, PIT, calibration, sharpness) against stationary bootstrap nulls.
- [ ] **SL-15-07** Detect model exploitation via adversarial optimizer: optimize a policy in learned world and test it in independent replay simulator.
- [ ] **SL-15-08** Use pessimistic rollout values when model uncertainty grows with planning horizon.
- [ ] **SL-15-09** Implement ensembles with disagreement-triggered policy fallback rather than fabricated confidence.
- [ ] **SL-15-10** Keep train-only model fitting, validation-only tuning and one-time protected holdout for final performance.
- [ ] **SL-15-11** Include cross-validation across independent liquidity and stress regimes, not only next-bar one-step prediction.
- [ ] **SL-15-12** Run no-trade randomized agent on model and real replay; reject world model that manufactures systematic easy profit.
- [ ] **SL-15-13** Validate path-level skew, tail, autocorrelation, gap and spread processes rather than just RMSE.
- [ ] **SL-15-14** Introduce multi-step lookahead only if benefit survives conservative evaluation of model forecast degradation.
- [ ] **SL-15-15** Test offline policy ranking sensitivity to alternative world models and parameter bootstrap.
- [ ] **SL-15-16** Log `WORLD_MODEL_ONLY` evidence status for synthetic rollouts and prohibit promotion based on modeled trajectories alone.
- [ ] **SL-15-17** Create counterfactual reconstruction of hypothetical decisions with explicit uncertainty and observability limits.
- [ ] **SL-15-18** Retire models that become overconfident or fail basic distributional calibration even if their induced simulated trades show profit.

**Wave acceptance:** Simulator-based planning cannot self-certify profit; policy performance must survive independent replay and forward results.


## SL-16. Hierarchical RL, regime routing and desk orchestration — P1

**Ownership:** `learning/hierarchy`  
**Definition of outcome:** Learn which strategy family and trade horizon to use without hiding correlated exposures.

- [ ] **SL-16-01** Define hierarchy levels: global risk governor → desk budget → strategy family → instrument proposal → execution timing.
- [ ] **SL-16-02** Build top-level regime labels from point-in-time volatility, breadth, liquidity, events and session microstructure.
- [ ] **SL-16-03** Use probabilistic regime belief instead of forcing every market period into a hard bull/bear label.
- [ ] **SL-16-04** Implement hidden-state/hysteresis filter to prevent rapid regime flip-flop and repeated churning.
- [ ] **SL-16-05** Add hierarchical option-policy abstraction with start/termination conditions and defined time-to-live.
- [ ] **SL-16-06** Include `CASH`, `WAIT`, `CLOSE` as globally available actions independent of alpha candidates.
- [ ] **SL-16-07** Implement regime-conditioned selector only after global simple selector baseline is measured.
- [ ] **SL-16-08** Use policy constraints to avoid simultaneous duplicated momentum bets and overlapping Nifty/BankNifty directional positions.
- [ ] **SL-16-09** Add parent-level total capital, sector, beta and downside exposure accounting across all five desks.
- [ ] **SL-16-10** Compare risk-budgeted strategy mixture with learned mixture and equal-weight/cash baselines.
- [ ] **SL-16-11** Store parent and child proposed actions and risk-governor projected final action in one linked decision chain.
- [ ] **SL-16-12** Prevent time-scale aliasing: monthly momentum reward must not influence a 15-minute option policy before outcome is realized.
- [ ] **SL-16-13** Lock strategy and regime routers by version during a forward evaluation cohort.
- [ ] **SL-16-14** Use regime-specific uncertainty minimum samples; fallback to global policy when regime sample is inadequate.
- [ ] **SL-16-15** Test abrupt state changes, overnight shock, holiday schedules, index reconstitution and options-expiry transition.
- [ ] **SL-16-16** Run hierarchical ablations showing which layer changes returns after costs versus only re-labels old trades.
- [ ] **SL-16-17** Build family-level negative correlation and true diversification diagnostics after controlling market/sector beta.
- [ ] **SL-16-18** Publish `NO_DIVERSIFIED_EDGE` if all positive candidates represent one factor with no independent benefit.

**Wave acceptance:** Hierarchical selector improves independent OOS risk-adjusted economics or is rejected; no hidden leverage by splitting across desks.


## SL-17. Online learning, incremental updates and bounded adaptation — P1

**Ownership:** `learning/online`  
**Definition of outcome:** Enable continuous improvement in shadow with frozen production decisions and measurable safe promotion.

- [ ] **SL-17-01** Build append-only outcome ingestion that joins forecast, decision, actual fill, mark, realized costs and final horizon reward.
- [ ] **SL-17-02** Compute prequential one-step performance: predict first, commit prediction, observe later, score, then update the shadow learner.
- [ ] **SL-17-03** Introduce delayed outcome settlement queue for 15-minute options, intraday positions, multi-day swings and monthly books.
- [ ] **SL-17-04** Implement bounded incremental logistic/linear model updates and contextual bandit posterior updates with versioned snapshots.
- [ ] **SL-17-05** Prohibit weight updates for live-cash policy without a separately approved new version and release gate.
- [ ] **SL-17-06** Use sliding-window, exponential decay and frozen-window alternatives as predeclared candidate policies.
- [ ] **SL-17-07** Create drift detectors for feature distribution, label distribution, calibration, execution quality and realized P&L independently.
- [ ] **SL-17-08** Apply drift alarms to reduce confidence or route to CASH; never automatically raise risk as response to losses.
- [ ] **SL-17-09** Study ADWIN/CUSUM/Page-Hinkley style detectors in-house with false-alarm benchmarks on autocorrelated financial data.
- [ ] **SL-17-10** Distinguish normal statistical variance from structural change using regime labels and confidence intervals.
- [ ] **SL-17-11** Freeze model on holidays, disconnected feed, missing settlement or corrupted history; no synthetic update.
- [ ] **SL-17-12** Use shadow-only challenger that continuously learns while champion operates unchanged during evaluation period.
- [ ] **SL-17-13** Implement checkpoint-and-rollback with exact weights, feature versions, RNG states, data watermarks and causal outcome cutoff.
- [ ] **SL-17-14** Define min effective sample size and independent period requirements before retrained policy gets an evaluation slot.
- [ ] **SL-17-15** Log every online update with hashed parent checkpoint and changed parameters; document why update occurred.
- [ ] **SL-17-16** Run sequential inference with alpha-spending/e-values or frozen review windows; avoid repeated peeking into same forward results.
- [ ] **SL-17-17** Build concept-drift negative controls where apparent profitable short windows revert to mean.
- [ ] **SL-17-18** Require owner review and new eligible cohort to change active strategy beyond current constraints.

**Wave acceptance:** Shadow learner updates causally and reversibly; no silent self-deployment, risk increase or mutation of a running cohort.


## SL-18. H005/H003/P3 momentum RL case study and live candidate — P0

**Ownership:** `learning/strategies/momentum`  
**Definition of outcome:** Use the repository's strongest historical candidate family for the first measurable self-learning experiment.

- [ ] **SL-18-01** Import H005/H003/P3 registered methodology and exact transaction schedules from development branches, retaining separate version IDs.
- [ ] **SL-18-02** Reconcile H003 universe contamination: reported ETF/MF holdings must be classified by asset class and evaluated using correct desk policies.
- [ ] **SL-18-03** Lock point-in-time candidate universe, month-end decision time, rebalance date, whole-share constraints and investable-capital assumptions.
- [ ] **SL-18-04** Baseline each candidate versus cash, Nifty 500 TRI, sector/factor matched book, fixed-turnover random books and existing fixed policy.
- [ ] **SL-18-05** Validate dividend-adjusted prices, delisted instruments and corporate actions before any new optimistic model selection.
- [ ] **SL-18-06** Implement per-name feature vector for momentum persistence, 52-week proximity, industry breadth, volatility and liquidity.
- [ ] **SL-18-07** Create baseline book with exact existing H005 top-three industries and 20-stock selection, no hindsight changes.
- [ ] **SL-18-08** Create `CASH/H005/H003/P3` contextual strategy selector trained on past month outcomes, not daily prices from current month.
- [ ] **SL-18-09** Create rule-based volatility scaling comparison and bounded RL sizing challenger under fixed total portfolio risk budget.
- [ ] **SL-18-10** Compare risk-aware reallocations against registered unchanged strategies to separate true adaptive gain from sample selection.
- [ ] **SL-18-11** Include brokerage, STT, fixed depository charges, stamp duty, taxes diagnostics and rebalance impact for account-size scenarios.
- [ ] **SL-18-12** Calculate net benchmark excess and conservative downside intervals across independent monthly periods.
- [ ] **SL-18-13** Store delayed-return reward only after the monthly evaluation period; no credit for unrealized or future returns at selection.
- [ ] **SL-18-14** Build corporate-event exclusion as independent testable rule with separate preregistration and search cost.
- [ ] **SL-18-15** Use frozen historical candidate runs for training only where allowed; new protected results require owner-authorized trial budget.
- [ ] **SL-18-16** Launch shadow-only monthly selector against fixed H005/H003/P3 paper cohorts with identical data/economics.
- [ ] **SL-18-17** Detect exposure overlap and state when performance is largely one momentum beta rather than three unrelated alphas.
- [ ] **SL-18-18** Degrade strategy to CASH or baseline on insufficient support, stale inputs, dividend anomalies or failed risk review.
- [ ] **SL-18-19** Publish causal month-by-month forecast, holdings, decision, costs, rejected alternatives, realized net excess and drawdown.
- [ ] **SL-18-20** No real-money promotion until independent forward periods satisfy predeclared evidence standard and owner/broker gates.

**Wave acceptance:** Adaptive selector must outperform frozen momentum champion on valid future monthly periods net of costs, or remain a rejected challenger.


## SL-19. Nifty 15-minute index forecasting and option policy learning — P1

**Ownership:** `learning/strategies/index_options`  
**Definition of outcome:** Connect calibrated 15-minute directional distributions to realistic option premiums and exits.

- [ ] **SL-19-01** Reuse existing `forecasting/index_15m.py` in five-desk branch; do not rewrite before parity audit.
- [ ] **SL-19-02** Create index universe from effective-dated NSE derivative instruments; unsupported Nifty indices receive research signals only.
- [ ] **SL-19-03** Create completed 15-minute bars on exchange-local sessions and labels for UP/DOWN/FLAT with train-only noise band.
- [ ] **SL-19-04** Build causal cross-index, VIX, breadth, constituent, futures basis and permitted option quote features with availability stamps.
- [ ] **SL-19-05** Benchmark index direction against class-frequency, random-walk, drift and calibrated simple supervised forecasts.
- [ ] **SL-19-06** Predict return distributions and volatility as separate targets; a direction label is not a trade return.
- [ ] **SL-19-07** Implement instrument/expiry/strike lot-resolution from current contract master and reject stale/discontinued option symbols.
- [ ] **SL-19-08** Construct option bid/ask, IV/skew, theta, delta, vega, gamma and depth snapshot by contract at the decision time.
- [ ] **SL-19-09** Use coherent option-pricing scenarios under underlying move, IV change, time decay and no-arbitrage consistency checks.
- [ ] **SL-19-10** Define option action `NO_TRADE`, long CE, long PE, close, reduce, and permitted defined-risk debit spread; disable naked short premium.
- [ ] **SL-19-11** Build per-contract expected net P&L and downside distribution after executable spread, fees, exit liquidity and time-to-expiry.
- [ ] **SL-19-12** Predeclare premium-based SL, TP, time-exit and risk-exit, not just underlying index points.
- [ ] **SL-19-13** Build horizon-specific risk-aware contextual bandit for contract/no-trade choice before introducing full sequential RL.
- [ ] **SL-19-14** Train offline option RL only on true bid/ask trajectories and observed action support; disallow fantasy fills from option midpoint.
- [ ] **SL-19-15** Monitor open positions faster than the 15-minute forecast clock; hard exit guards remain outside RL.
- [ ] **SL-19-16** Aggregate correlated index bets through a single delta/vega/gamma and account risk budget.
- [ ] **SL-19-17** Implement expiry-day and overnight gap stress, ITM settlement, OI loss, quote outage and limit order non-fill cases.
- [ ] **SL-19-18** Compare forecast-only, fixed-rule options trade, bandit and conservative RL on same protected prospective period.
- [ ] **SL-19-19** Document exact paper sources and permissions; new forward quote use stays disabled if data-rights unresolved.
- [ ] **SL-19-20** Publish standalone option premium P&L per actual contract with fill provenance and fail all claims of profit from mere directional accuracy.

**Wave acceptance:** Nifty options agent gets paper eligibility only after full quote-based premium P&L, conservative exits and credible OOS/forward evidence.


## SL-20. Execution timing and order placement learning — P1

**Ownership:** `learning/execution`  
**Definition of outcome:** Optimize how an approved trade is implemented, not whether unapproved signals should generate orders.

- [ ] **SL-20-01** Define child action space: marketable limit, patient limit, cancel/replace, wait, and partition size within venue constraints.
- [ ] **SL-20-02** Make approved parent order and quantity immutable input; execution RL may not raise risk or invent new directional trades.
- [ ] **SL-20-03** Define execution state: spread, depth, queue estimates, order age, participation, shortfall, available time and partial fills.
- [ ] **SL-20-04** Define execution reward as negative actual implementation shortfall and fees with non-fill penalty tied to parent alpha decay.
- [ ] **SL-20-05** Implement deterministic execution baseline marketable-limit, TWAP and fixed participation before learning.
- [ ] **SL-20-06** Build limit-fill likelihood calibrated only on observed order/trade/quote history; no guaranteed queue priority.
- [ ] **SL-20-07** Create safe replay for quote changes, gaps, quote crossing, stale depth, cancels, races and partial executions.
- [ ] **SL-20-08** Keep fill simulation fidelity labels distinct from broker-executed records.
- [ ] **SL-20-09** Train contextual bandit for timing/limit choices with realistic late/no-fill outcomes and explicit action support.
- [ ] **SL-20-10** Implement conservative discrete RL only when sequences include adequate order-state behavior coverage.
- [ ] **SL-20-11** Compare order-size and scheduling optimization on equal desired quantity and urgency.
- [ ] **SL-20-12** Add adverse selection and post-fill markout scoring for 1/5/15-minute horizons.
- [ ] **SL-20-13** Model per-venue participation and account capital constraints so agent cannot trade with unreserved cash.
- [ ] **SL-20-14** Enforce order timeout, idempotent cancel/reconciliation and prevention of double fill after lost acknowledgment.
- [ ] **SL-20-15** Test execution agent cannot improve rewards by never trading a valid parent order without being penalized appropriately.
- [ ] **SL-20-16** Expose completion rate, mean/p95 slippage, error rates, capacity and tail shortfall under stress.
- [ ] **SL-20-17** Inject sudden spread widening, partial book snapshots and missing quotes; return UNKNOWN/CANCEL rather than fake fill.
- [ ] **SL-20-18** Make new execution policy a distinct promoted artifact from alpha policy; train independently and freeze per session.

**Wave acceptance:** Execution policy reduces empirically measured cost on matched parent intents without violating fill/ledger safety invariants.


## SL-21. ETF, mutual fund, swing and futures learning adapters — P1

**Ownership:** `learning/desks`  
**Definition of outcome:** Use one learning framework while respecting genuinely different instruments and execution clocks.

- [ ] **SL-21-01** Build ETF state/actions: executable quote premium to iNAV, spread, tracking, benchmark, cash and buy/sell/hold.
- [ ] **SL-21-02** Use ETF NAV/iNAV only as timely reference features; actual exchange fill uses bid/ask and depth.
- [ ] **SL-21-03** Implement equity swing long/short eligibility separately, with borrow/margin rules and multi-day gap risk.
- [ ] **SL-21-04** Build conventional MF decision actions `ADD/HOLD/REDUCE/REDEEM/SWITCH` at NAV cutoff, not instant market orders.
- [ ] **SL-21-05** Create MF reward from actual applicable NAV allotment/redemption timing, exit load, eligible units and cash settlement.
- [ ] **SL-21-06** Add SIP/STP/SWP optimizer as recommendation assistant with fixed cash-flow constraints; no invented intraday stop-loss.
- [ ] **SL-21-07** Support multi-day equity opportunity costs, tax-lot effects and cash drag in portfolio reward.
- [ ] **SL-21-08** For futures, model contract rollover, daily mark-to-market, margin sufficiency and expiry with effective dated specifications.
- [ ] **SL-21-09** Introduce shared portfolio exposure mapping across stock, option delta, ETF underlying, MF holdings and futures.
- [ ] **SL-21-10** Construct desk-specific benchmark, risk constraints and return sampling frequency in policy evaluation.
- [ ] **SL-21-11** Use cross-desk RL only when reward normalization and common cash ledger are validated.
- [ ] **SL-21-12** Keep MF/ETF drift signals from causing forced unnecessary trades when fees or settlement favor holding.
- [ ] **SL-21-13** Disable strategies without operationally permitted data and instrument capabilities.
- [ ] **SL-21-14** Test NAV publication delays and fund holiday calendar differences with market trading dates.
- [ ] **SL-21-15** Compare advanced desk RL against simple risk-aware allocation, cost-aware rebalance and passive reference.
- [ ] **SL-21-16** Store trade and recommendation reasoning with relevant human approval and no automatic redemption of real investments.
- [ ] **SL-21-17** Build per-desk standalone audit: paper cash, NAV, settled units, exposures, pending instructions and net returns.
- [ ] **SL-21-18** Publish combined capital allocation report excluding incomparable hypothetical returns and duplicated exposure.

**Wave acceptance:** Each desk has an independent causal data/trading contract; unified learning never converts MF recommendations into exchange orders.


## SL-22. Adaptive portfolio allocation, covariance and uncertainty — P1

**Ownership:** `learning/portfolio`  
**Definition of outcome:** Learn sizing and capital efficiency without assuming Sharpe-optimal weights remain stable.

- [ ] **SL-22-01** Implement long-only/short-enabled feasible weight projection with desk-specific limits and signed exposure accounting.
- [ ] **SL-22-02** Derive target portfolio weights from net expected excess, covariance and estimation uncertainty, subject to fixed risk budget.
- [ ] **SL-22-03** Use robust shrinkage covariance, factor exposure and volatility scaling baselines before black-box allocation.
- [ ] **SL-22-04** Implement cross-desk overlap measures for H005/H003/P3, Nifty options, ETFs and overlapping MF holdings.
- [ ] **SL-22-05** Compute marginal risk contribution, factor exposure, expected shortfall and concentration under current and shocked correlation.
- [ ] **SL-22-06** Add turnover/cost-aware rebalancing with no-trade bands and fixed-charge effects.
- [ ] **SL-22-07** Model cash and opportunity cost explicitly; learner must choose cash when no candidate clears hurdle.
- [ ] **SL-22-08** Build contextual allocator for strategy sleeves with delayed rewards and frozen broker-independent order intents.
- [ ] **SL-22-09** Add uncertainty lower-confidence bound to expected return input and suppress excessive risk under weak evidence.
- [ ] **SL-22-10** Implement drawdown-aware deleveraging hard caps, not automatic doubling-down after losses.
- [ ] **SL-22-11** Create scenario optimizer under sector shocks, index crash, option gamma shock and funding constraints.
- [ ] **SL-22-12** Compare equal-risk, equal-weight, volatility-targeted, robust mean-variance and RL allocation on identical data.
- [ ] **SL-22-13** Include taxes, whole shares, margin, settlement and leverage financing in capacity optimization.
- [ ] **SL-22-14** Add periodic rebalancing cadence and event-triggered risk exits with timestamp audit.
- [ ] **SL-22-15** Cap daily policy turnover and commissions separately from per-trade cost penalty.
- [ ] **SL-22-16** Enforce one aggregate loss/risk budget across all active desks with fail-closed state on ledger uncertainty.
- [ ] **SL-22-17** Decompose reported alpha into strategy selection, sizing, market beta, timing, execution and tax drag.
- [ ] **SL-22-18** Prohibit policy updates from changing past planned positions in the journal or simulating nonexistent fills.

**Wave acceptance:** Policy gains are positive after matched risk/turnover costs, and all holdings/cash reconcile to one account.


## SL-23. Regime drift, adversarial robustness and nonstationary markets — P1

**Ownership:** `learning/robustness`  
**Definition of outcome:** Explicitly detect and survive structural market changes rather than assuming stable reward dynamics.

- [ ] **SL-23-01** Build taxonomy for bull, bear, high vol, low vol, liquidity dry-up, expiry shock, event shock and abnormal gap regimes.
- [ ] **SL-23-02** Estimate regime only from currently available features with an unknown-regime fallback.
- [ ] **SL-23-03** Create training/validation splits with separate stress years, sector cycles, liquidity classes and structural break segments.
- [ ] **SL-23-04** Use online drift monitoring for feature calibration, forecast confidence, fill realism and net P&L.
- [ ] **SL-23-05** Validate drift false positives and detection delay on block-correlated synthetic processes.
- [ ] **SL-23-06** Add monitoring for model overconfidence after regime change and loss of feature predictive power.
- [ ] **SL-23-07** Create adversarial price paths consistent with observed gaps/volatility; do not assert they are probable forecasts.
- [ ] **SL-23-08** Perturb fees, tick size, quote latency, bid-ask spread, contract availability and market participation.
- [ ] **SL-23-09** Model market-wide crashes and crowded momentum unwind; check whether correlated desks all fail simultaneously.
- [ ] **SL-23-10** Test policy under lost history, partially missing options chains, stale earnings filings and revision storms.
- [ ] **SL-23-11** Compare fixed champion, online bandit, meta learner and regime router over same stress matrix.
- [ ] **SL-23-12** Include structural regime decay review; automatically de-risk but do not self-certify a revised strategy.
- [ ] **SL-23-13** Implement portfolio harm-stop based on economic losses, operational anomalies and evidence-quality degradation.
- [ ] **SL-23-14** Use portfolio worst-case and posterior uncertainty to limit size; never increase risk on insufficient sample.
- [ ] **SL-23-15** Create adversarial evaluator separate from candidate authorship and training pipeline.
- [ ] **SL-23-16** Keep `REGIME_UNKNOWN` a valid state that leads to fallback policy or abstention.
- [ ] **SL-23-17** Distinguish changes in price regime from changes in the provider market-data distribution.
- [ ] **SL-23-18** Publish stress survival table and exact candidate failure causes, not a single robustness score.

**Wave acceptance:** Known shocks and large data anomalies de-risk or abstain; no automatic uncontrolled risk seeking to recover losses.


## SL-24. Off-policy evaluation, causal counterfactuals and selection bias — P0

**Ownership:** `learning/evaluation`  
**Definition of outcome:** Prevent self-learning agents from gaming historical evaluation and claiming improvements unsupported by data.

- [ ] **SL-24-01** Define observed on-policy evaluation, matched counterfactual replay and logged-policy off-policy evaluation as different evidence types.
- [ ] **SL-24-02** Implement direct method, IPS, self-normalized IPS and doubly robust estimators only where action support/propensities exist.
- [ ] **SL-24-03** Calculate effective sample size, maximum importance weight and positivity/support diagnostics; refuse invalid estimates.
- [ ] **SL-24-04** Implement propensity clipping and sensitivity analysis as registered variants, never after seeing favorable results.
- [ ] **SL-24-05** Validate DR estimators on synthetic bandit environment with known exact value and deliberately misspecified outcome/propensity models.
- [ ] **SL-24-06** For sequential RL implement appropriate trajectory or per-decision importance weights with variance bounds; abstain when variance explodes.
- [ ] **SL-24-07** Use fitted Q evaluation with independent models and bootstrap as supplementary not definitive when support is weak.
- [ ] **SL-24-08** Distinguish replay-derived counterfactual fills from actual observed fills, preserving simulator fidelity.
- [ ] **SL-24-09** Calculate matched random-book nulls with same turnover, exposure, sector, liquidity, instrument availability and hold period.
- [ ] **SL-24-10** Create nested chronological training/validation/test and purge/embargo where rewards overlap.
- [ ] **SL-24-11** Use clustered confidence intervals at session and month level, not as independent overlapping minute/trade rows.
- [ ] **SL-24-12** Use deflated Sharpe, familywise correction, SPA/Reality Check and PBO where their assumptions are satisfied.
- [ ] **SL-24-13** Count all variants generated by AI, hyperparameter runs, prompts and failed hypotheses in experiment multiplicity.
- [ ] **SL-24-14** Implement sequential test governance with frozen review cadence, alpha spending or preapproved evidence windows.
- [ ] **SL-24-15** Maintain untouched held-out samples and record one-time opening; once viewed they become development data.
- [ ] **SL-24-16** Declare `NO_VALID_OPE` and require forward shadow instead of computing unsupported counterfactual returns.
- [ ] **SL-24-17** Compare economic outcomes against cash, matched TRI and risk-normalized champion with all fees, partial fills and open marks.
- [ ] **SL-24-18** Include selection bias from always choosing the strongest recent strategy in simulations and null controls.
- [ ] **SL-24-19** Publish evidence tier, independent sample count, effect CI, cost-stress pass and contradictory null tests for every candidate.
- [ ] **SL-24-20** Automate falsification reviewer verdict; candidate generator has zero authority to waive failure.

**Wave acceptance:** A simulated random strategy family does not systematically pass; unsupported propensities and repeated holdout peeking fail closed.


## SL-25. Champion–challenger governance and automated retraining — P0

**Ownership:** `learning/promotion`  
**Definition of outcome:** Permit automated learning continuously while preserving independent, reviewable promotion boundaries.

- [ ] **SL-25-01** Implement append-only PolicyRegistry with `CANDIDATE`, `OOS_PASSED`, `SHADOW`, `FORWARD_UNPROVEN`, `REVIEW_ELIGIBLE`, `RETIRED`, `BLOCKED`.
- [ ] **SL-25-02** Create immutable policy artifact binding code hash, weights, reward, training data hash, feature versions, risk caps, source rights and evaluation protocol.
- [ ] **SL-25-03** Freeze champion weights, feature logic and decision clock for each prospective cohort.
- [ ] **SL-25-04** Run challenger and champion on same eligible market snapshots with identical capital, costs and refusal rules.
- [ ] **SL-25-05** Create paired-session net-return differences including cash days and unresolved positions rather than cherry-picked trades.
- [ ] **SL-25-06** Require pre-registered superiority or non-inferiority margin and dependence-aware lower confidence bound for champion replacement.
- [ ] **SL-25-07** Implement risk-based automatic demotion when current champion breaches a hard operational or financial safety gate.
- [ ] **SL-25-08** Allow automatic *reduction* of risk or return to cash on degradation; no automatic increase in cash exposure limits.
- [ ] **SL-25-09** Add model registry compare command returning the exact evidence for score deltas and confidence.
- [ ] **SL-25-10** Prevent strategy-generator or LLM from approving its own output, falsification test or live-stage promotion.
- [ ] **SL-25-11** Build retrain policy that does not overwrite champion checkpoint; it produces a new immutable candidate artifact.
- [ ] **SL-25-12** Separate scheduled retraining from performance-triggered retraining and count each as a new selection decision.
- [ ] **SL-25-13** Include strict sequential testing review frequency; a thousand daily comparisons cannot be treated as a single independent experiment.
- [ ] **SL-25-14** Implement rollback to prior champion or CASH with deterministic state transfer and no double execution of pending orders.
- [ ] **SL-25-15** Expose `EVIDENCE_INSUFFICIENT` when replay advantage is positive but forward sample too short.
- [ ] **SL-25-16** Archive every candidate, including harmful models, with metadata needed to reproduce why they were rejected.
- [ ] **SL-25-17** Include owner-controlled release sign-off and explicit effective time; policy switch mid-session is forbidden by default.
- [ ] **SL-25-18** Test false promotion, reward tampering, changed risk cap, unsigned artifact, lost journal and expired owner authorization.

**Wave acceptance:** No autonomous policy can promote itself, move to LIVE_CASH or rewrite the champion history; every upgrade has independently reproduced evidence.


## SL-26. Autonomous research agent orchestration and controlled code generation — P1

**Ownership:** `agents/alpha_research`  
**Definition of outcome:** Let AI generate ideas and implementations while validators enforce objective evidence standards.

- [ ] **SL-26-01** Define deterministic planner -> hypothesis author -> implementation author -> unit-test author -> independent critic -> evidence evaluator -> registry workflow.
- [ ] **SL-26-02** Implement read-only research context exposing allowed dataset catalogs, recorded failed trials, existing code, economics and relevant research notes.
- [ ] **SL-26-03** Construct typed tool permissions: generate DSL proposal, write sandbox code, run permitted backtest, request review, never place live orders.
- [ ] **SL-26-04** Allow generation of baseline strategies only as new `hypothesis_id` descendants with preserved search-family lineage.
- [ ] **SL-26-05** Require candidate author to cite data availability, economic mechanism and how it differs from failed previous versions.
- [ ] **SL-26-06** Implement structured self-critique for lookahead, costs, multiple testing, survivorship, expected capacity and research novelty.
- [ ] **SL-26-07** Use an independent evaluator with access to outcome metrics only after candidate/test contract is sealed.
- [ ] **SL-26-08** Create clean-room scratch worktrees and denied-access protected holdout datasets for unapproved agents.
- [ ] **SL-26-09** Generate first-party Python code or strategy DSL under pinned static imports with no external network or credential access.
- [ ] **SL-26-10** Execute in isolated child process with CPU, memory, disk, wall-clock, file and import allowlists; no access to real portfolio data outside scoped snapshots.
- [ ] **SL-26-11** Implement human review required for new data sources, high-risk derivatives, leverage, changed trial budgets or new capital levels.
- [ ] **SL-26-12** Add prompt-injection defenses for filings, websites, logs and adversarial market-news text; model outputs are untrusted data.
- [ ] **SL-26-13** Keep trace of prompts, tool calls, code diffs, failed tests, corrected versions and reasons for rejection.
- [ ] **SL-26-14** Enforce independent baseline reproduction before accepting any agent-created new algorithm as scientifically novel.
- [ ] **SL-26-15** Build output verifier that compares submitted backtest metrics with recomputation from raw trade ledger.
- [ ] **SL-26-16** Create automated negative tests where an agent can improve Sharpe only by leakage or fabricated fills; evaluator must catch it.
- [ ] **SL-26-17** Implement resource allocation to promising but under-tested hypotheses, not endless variants of successful in-sample patterns.
- [ ] **SL-26-18** Archive `NO_EDGE_FOUND` and `REJECTED` research outcomes as useful discoveries, not failures to be erased.
- [ ] **SL-26-19** Expose a one-command campaign rehearsal that builds/validates candidates on fixtures without protected or live market access.
- [ ] **SL-26-20** Disallow autonomous rewriting of ADRs, audit baselines, external entitlement flags, approved orders or historical data.

**Wave acceptance:** A fully scripted agent can propose/test/reject a hypothesis in a sandbox but cannot access secrets, buy anything or bypass scientific gates.


## SL-27. Multi-agent critics, memory and scientific audit trails — P1

**Ownership:** `agents/verification`  
**Definition of outcome:** Use independent specialists without converting their agreement into evidence.

- [ ] **SL-27-01** Assign nonoverlapping roles: macro/market researcher, data auditor, statistical reviewer, execution engineer, risk critic and experiment registrar.
- [ ] **SL-27-02** Create evidence graph from hypothesis → inputs → model → backtest → critiques → outcomes → promotion decision.
- [ ] **SL-27-03** Build immutable decision memory for each agent with versioned model state, known data as-of, contradictions and uncertainty.
- [ ] **SL-27-04** Store postmortems for Four-Node, indicator-only intraday, VWAP and other failed strategy families to prevent rediscovery loops.
- [ ] **SL-27-05** Implement failure-taxonomy learning: leakage, impossible fill, fees, missing rights, invalid null, overfit, low power, market regime shift.
- [ ] **SL-27-06** Use independent critic prompts/rules for economic mechanisms, causal correctness, code safety, and mathematical evaluation.
- [ ] **SL-27-07** Keep independent reviewer from reading candidate author's private training result labels when reviewing protocol.
- [ ] **SL-27-08** Develop disagreement escalation to human review or `HOLD`, never average unsafe proposals into an approved trade.
- [ ] **SL-27-09** Create exact reproducibility packet for accepted and rejected hypotheses with model checkpoint and raw source snapshots.
- [ ] **SL-27-10** Implement cross-project separation: no investing/finance agent uses unrelated personal secrets as trading features.
- [ ] **SL-27-11** Make memory updates append-only with correction records, not mutable narratives that change prior evidence.
- [ ] **SL-27-12** Enable automated literature comparison via safely obtained public references; literature popularity does not confer alpha.
- [ ] **SL-27-13** Publish critique coverage: number of detected defects by source family and false approval rate on injected cheating examples.
- [ ] **SL-27-14** Create human-readable causal evidence report distinguishing backtest association from observed economic mechanism.
- [ ] **SL-27-15** Detect and flag majority-vote hallucination: five agents agreeing without any additional market data remains zero empirical evidence.
- [ ] **SL-27-16** Create privacy/security controls for financial account holdings and broker credentials in logs and agent prompts.
- [ ] **SL-27-17** Implement source rights separation for proprietary reports and model weights; no covert redistribution.
- [ ] **SL-27-18** Preserve a historical audit of what each agent believed before seeing outcomes for prospective assessment.

**Wave acceptance:** Reviewer catches intentionally seeded financial/technical defects and all final claims trace to measured artifacts, not agent consensus.


## SL-28. Observability, dashboard, explanations and user controls — P1

**Ownership:** `product/learning_console`  
**Definition of outcome:** Make every learning claim inspectable with source, uncertainty and economic status.

- [ ] **SL-28-01** Build Learning Control Room separate from trader order UI, with clear RESEARCH/SHADOW/PAPER/REAL labels.
- [ ] **SL-28-02** Display candidate funnel counts by created, registered, tested, failed, shadowed, forward-reviewed and retired.
- [ ] **SL-28-03** Show champion/challenger paired cumulative net excess, drawdown, costs, turnover and uncertainty intervals.
- [ ] **SL-28-04** Display reward decomposition and count of unresolved orders before any profit headline.
- [ ] **SL-28-05** Expose 15-minute Nifty forecast probabilities and calibration separately from option CE/PE decisions and premium P&L.
- [ ] **SL-28-06** Provide per-asset-class ledger, instrument exposure, capital reserved and available cash, including pending MF states.
- [ ] **SL-28-07** Create confidence and evidence-tier badges that cannot be set by a strategy author or free-text agent response.
- [ ] **SL-28-08** Show performance by regime, sector, liquidity, month, holding horizon and capital size with adequate sample warnings.
- [ ] **SL-28-09** Display date of latest valid market quote, paper run completion, strategy version, feature freshness and source entitlement.
- [ ] **SL-28-10** Provide an Explain Decision panel: all alternatives, expected net alpha, why no trade, invalidation, SL/TP and required approvals.
- [ ] **SL-28-11** Build failure analysis browser for historical candidate loss decomposition and false positive root causes.
- [ ] **SL-28-12** Provide owner-only controls to halt paper sessions, lower risk, freeze retraining, mark evidence invalid or retire a policy.
- [ ] **SL-28-13** Require explicit review for any action that would increase size, leverage or exposure; prohibit hidden auto-approval.
- [ ] **SL-28-14** Add trustworthy stale-data/offline-state UI, and prevent the screen from displaying last quote as current executable market.
- [ ] **SL-28-15** Build daily/monthly research digest showing candidates discovered, real results, costs, operational defects and loss-limit state.
- [ ] **SL-28-16** Provide exportable JSON/CSV/HTML/PDF-compatible scorecards from the same source-of-truth ledger with versioned report IDs.
- [ ] **SL-28-17** Add user journey tests to distinguish a research suggestion from an actionable, approved instrument-specific signal.
- [ ] **SL-28-18** Measure utility by false trade recommendations prevented, reproducible evaluations and correct abstention, not dashboard activity.

**Wave acceptance:** A tester can trace any displayed profit to a verified economic ledger and identify unsupported/self-reported strategy evidence.


## SL-29. Forward paper operations, shadow cohorts and custody — P0

**Ownership:** `operations/forward`  
**Definition of outcome:** Generate genuine future-session evidence without disguising local replay as independently observed fills.

- [ ] **SL-29-01** Create market-session preflight validating clock, quotes, licensed feed permissions, source metadata, open positions and kill switch.
- [ ] **SL-29-02** Write planned cohort registration and policy hash before any real market event reaches research evaluation.
- [ ] **SL-29-03** Collect new quote/order/decision sequences with source/receive timestamps and permitted retention into append-only stores.
- [ ] **SL-29-04** Attach evidence tier `LOCAL_PAPER`, `INDEPENDENT_FORWARD` only when separately verified custody actually exists.
- [ ] **SL-29-05** Record every opportunity rejected for risk, cost, insufficient confidence, stale data and no-trade choices.
- [ ] **SL-29-06** Run H005/H003/P3 champion and learner shadow cohorts with shared eligible universe and matched capital/economics.
- [ ] **SL-29-07** Run Nifty 15-minute forecaster in forecast-only shadow until direction calibration and net options decision validity pass.
- [ ] **SL-29-08** Monitor positions and precommitted exit rules continuously within permitted feed and session constraints.
- [ ] **SL-29-09** Persist intent before simulated execution, order state and fills before portfolio mutation, and post exactly-once fees.
- [ ] **SL-29-10** On feed outage, mark position OPEN/UNRESOLVED or defensibly marked; stale last quote is not a completed executable SELL.
- [ ] **SL-29-11** Implement recovery from process kill at any event boundary without duplicate fill, duplicate reward or lost stop obligation.
- [ ] **SL-29-12** Run end-of-day cash/position/fee/quantity/collateral reconciliation and produce a signed-or-hashed session report.
- [ ] **SL-29-13** Keep raw source event path and derived strategy judgment separately for independent replay and consistency checks.
- [ ] **SL-29-14** Include at least one all-cash session in validation and require no fictitious orders or success-rate manipulation.
- [ ] **SL-29-15** Store mark-to-market of open positions, pending orders and external flows across monthly boundaries.
- [ ] **SL-29-16** Build independent performance statistic jobs that never share or depend on agent-reported reward summaries.
- [ ] **SL-29-17** Handle scheduled market holidays, exchange disruption and options expiry correctly with visible session status.
- [ ] **SL-29-18** Enforce no direct order permission for agents/learners, including loss recovery or emergency override.
- [ ] **SL-29-19** Publish cumulative cohort-level returns net of all supported costs and benchmark, including missed/cancelled fills.
- [ ] **SL-29-20** Retain independently reviewable evidence that timestamps, quotes, actions, fills and scorecards were generated in that order.

**Wave acceptance:** A complete observed paper cohort is reproducible from immutable events; crash/outage scenarios cannot invent or delete P&L.


## SL-30. Safety, security, broker governance and compliance — P0

**Ownership:** `security/policy`  
**Definition of outcome:** Protect the research platform and future real-money boundary against agent/ML failure and malicious inputs.

- [ ] **SL-30-01** Define policy decision as advice until a separately trusted broker-side approval component authorizes an order.
- [ ] **SL-30-02** Implement hard-coded `PHYSICAL_ORDERS_ENABLED=false` in current learning execution stack with hostile network tests.
- [ ] **SL-30-03** Create independent human-signed approvals for any future broker stage, account, capital, asset classes and permission expiration.
- [ ] **SL-30-04** Keep broker keys out of research agents, generated code, experiment logs, dashboard JS and training datasets.
- [ ] **SL-30-05** Implement principle-of-least-privilege connectors for read-only market data versus portfolio observation versus restricted future order path.
- [ ] **SL-30-06** Check license/retention permissions for data in simulation, learning and forward paper mode before first read.
- [ ] **SL-30-07** Test source manipulation, forged ticks, replayed quotes, reordered event timestamps and intentional stale feed responses.
- [ ] **SL-30-08** Prevent runtime code generation, untrusted deserialization, subprocess escape and arbitrary model checkpoint execution.
- [ ] **SL-30-09** Check weights/artifact provenance and cryptographic hashes before loading an active model.
- [ ] **SL-30-10** Implement audit of dependencies, dynamic imports, model checkpoint integrity and CI provenance.
- [ ] **SL-30-11** Build risk governor as non-ML deterministic independently tested module that can halt all strategies.
- [ ] **SL-30-12** Enforce daily/session/account loss limits and position exposure across every desk with fail-closed handling.
- [ ] **SL-30-13** Log every risk-cap and model version change with identity, justification, approval and earliest effective time.
- [ ] **SL-30-14** Add adversarial prompt tests using malicious filing/news text asking agent to disable risk checks or output fake profit.
- [ ] **SL-30-15** Run simulation of rogue researcher producing thousands of near-identical variants to launder a false positive.
- [ ] **SL-30-16** Ensure execution cannot activate from label alone; confidence, reward or human-friendly prose never equals permission.
- [ ] **SL-30-17** Declare real-money readiness dependent on up-to-date market/broker rules and human legal review, not completion of software tasks.
- [ ] **SL-30-18** Provide emergency operation manual for stale feed, corrupt ledger, wrong contract, unauthorized order, position mismatch and active drawdown.

**Wave acceptance:** A malicious/reward-hacking learning agent cannot acquire secrets, bypass authorization, deploy new capital or disable the kill switch.


## SL-31. Reliability, compute efficiency and reproducible training operations — P1

**Ownership:** `mlops/training`  
**Definition of outcome:** Operate bounded learning workloads on affordable hardware without losing auditability.

- [ ] **SL-31-01** Build reproducible local Python 3.13/NumPy/Pandas/DuckDB environment and optional PyTorch training extra with locked versions.
- [ ] **SL-31-02** Provide CPU baseline learners capable of running on a 16GB RAM development machine with deterministic partitions.
- [ ] **SL-31-03** Use partitioned Parquet and memory-mapped datasets to avoid loading the full market tape into RAM.
- [ ] **SL-31-04** Implement streaming minibatches, sequence sharding and asynchronous prefetch only if deterministic sample-order metadata is retained.
- [ ] **SL-31-05** Add GPU optionality for offline RL neural models; never make the system depend on rented external intelligence APIs.
- [ ] **SL-31-06** Use single host lock and scheduler to prevent two learner processes mutating the same candidate/ledger.
- [ ] **SL-31-07** Checkpoint weights, optimizer, RNG, scheduler, progress, data version and training metrics atomically.
- [ ] **SL-31-08** Implement resumable training with exact parent checkpoint and byte-for-byte or tolerance-verified continuation.
- [ ] **SL-31-09** Create compute budgets for train/validate/falsify/candidate search, with automatic early-stop on failure.
- [ ] **SL-31-10** Profile cost per candidate and marginal information improvement relative to simpler features.
- [ ] **SL-31-11** Build model compression/distillation only after economic OOS results show a substantial advantage.
- [ ] **SL-31-12** Run strict CI for learning library: unit, numerical gradient, toy-env, deterministic training, OOD, data leakage and policy comparison tests.
- [ ] **SL-31-13** Create reproducible benchmark tasks from synthetic markets with analytic optimal decisions and known zero-alpha controls.
- [ ] **SL-31-14** Compare training speed, policy evaluation cost, cold-start latency and peak memory by algorithm/horizon.
- [ ] **SL-31-15** Add `--dry-run`, `--fixture-only`, `--offline`, `--shadow` and `--resume` flags with fail-closed defaults.
- [ ] **SL-31-16** Keep scheduler from causing order or model updates at market opens if preflight incomplete.
- [ ] **SL-31-17** Build machine-readable candidate manifests for exact rerun on Windows and Linux and standardized result archive.
- [ ] **SL-31-18** Create hardware performance report; no new accelerator purchase required until its bottleneck is quantified.

**Wave acceptance:** Full fixture workflow runs on available local compute; optional GPU increases speed but not epistemic evidence or authorization.


## SL-32. Numerical, adversarial and scientific acceptance suite — P0

**Ownership:** `tests/learning`  
**Definition of outcome:** Demonstrate correctness against known hard cases before asking market data to validate profits.

- [ ] **SL-32-01** Test positive-control deterministic market with known profitable policy recovers correct action without lookahead.
- [ ] **SL-32-02** Test fair random-walk market with positive costs yields no validated systematic trading edge.
- [ ] **SL-32-03** Test learned Q overestimation from unsupported actions causes fallback and candidate rejection.
- [ ] **SL-32-04** Test zero bid/ask depth, quote outage and spread widening cannot be represented as executable fills.
- [ ] **SL-32-05** Test reward does not improve if loser positions are left unmarked or hidden from ledger.
- [ ] **SL-32-06** Test delayed option expiry and monthly reward attribution: model cannot train on not-yet-observed P&L.
- [ ] **SL-32-07** Test all future data mutated after t has no impact on prior features, decisions or model updates.
- [ ] **SL-32-08** Test symbol changes, delisting, dividends, splits and expiring option tokens maintain correct historical identity.
- [ ] **SL-32-09** Test same-bar SL/TP conflict uses adverse-first or unresolved, never favorable hindsight ordering.
- [ ] **SL-32-10** Test different session time zones, market holidays and partial candles do not generate phantom signals.
- [ ] **SL-32-11** Test random candidate search of thousands of fake rules does not yield unadjusted promoted false edge.
- [ ] **SL-32-12** Test self-competition: independent evaluator and registry reject model that author edits after holdout results.
- [ ] **SL-32-13** Test OPE when behavior action propensities are missing, tiny or zero; output unsupported not fake significance.
- [ ] **SL-32-14** Test all-cash policy has exactly zero trade fees and every cash day is included in returns.
- [ ] **SL-32-15** Test negative benchmark alpha despite positive raw P&L remains a failure when benchmark-relative claim is made.
- [ ] **SL-32-16** Test offline sample density shift and unsupported high-leverage action cannot trigger an approved order.
- [ ] **SL-32-17** Test retry after crash at journal boundaries causes no duplicate cash movement, position, fill or learning reward.
- [ ] **SL-32-18** Test multimodel correlated strategy picks cannot violate global capital and tail-risk limits.
- [ ] **SL-32-19** Test permission denial for data sources and broker sends cannot be bypassed by generated code, prompt injections or model weights.
- [ ] **SL-32-20** Test rollback reinstates champion and accounts for pending simulated positions without changing their entry history.
- [ ] **SL-32-21** Test every research result produces exact reproducible input manifests and raw independent economic evaluator output.
- [ ] **SL-32-22** Include mutation testing and coverage targets for numerical methods, risk gates, OMS, strategy identity and evaluation honesty.

**Wave acceptance:** All security/ledger/causality tests pass; null-market false edge rate and positive-control recovery conform to predeclared simulation checks.


## SL-33. Automated self-learning release pipeline and end-to-end proof — P0

**Ownership:** `platform/release`  
**Definition of outcome:** Produce an operable autonomous research product that learns safely while earnings claims remain empirical.

- [ ] **SL-33-01** Build `fb learn audit` read-only baseline checker and refuse active release on missing source, CI, right or ledger gate.
- [ ] **SL-33-02** Build `fb learn discover --family ... --budget ...` producing proposals/registered manifests, not physical trades.
- [ ] **SL-33-03** Build `fb learn train --policy ... --mode offline` creating immutable checkpoints and train/validation scorecards.
- [ ] **SL-33-04** Build `fb learn evaluate --candidate ... --protocol ...` recomputing outcomes from ledger with selection correction.
- [ ] **SL-33-05** Build `fb learn falsify --candidate ...` applying nulls, shuffled signals, cost stress, capacity and causal leakage tests.
- [ ] **SL-33-06** Build `fb learn shadow --candidate ...` consuming only permitted market observations and appending decisions prospectively.
- [ ] **SL-33-07** Build `fb learn compare --champion ... --challenger ...` producing paired-session, matched-capital net economics.
- [ ] **SL-33-08** Build `fb learn propose-promotion --candidate ...` outputting full blockers and requesting human review; it never promotes directly.
- [ ] **SL-33-09** Build `fb learn monitor --desk all` displaying data, feature, model, economic and execution degradation without mutating strategy.
- [ ] **SL-33-10** Build `fb learn rollback --policy ...` authorized safety fallback with immutable event log.
- [ ] **SL-33-11** Implement end-to-end fixture campaign: auto-propose → register → train → falsify → shadow → reject known bad strategy.
- [ ] **SL-33-12** Implement synthetic positive-control fixture that passes only with genuinely causal advantage and realistic cost accounting.
- [ ] **SL-33-13** Run matched historical OOS of H005/H003/P3 selector with frozen methodology, logging all candidate versions.
- [ ] **SL-33-14** Run Nifty 15-minute forecast-only shadow cohort; permit option paper proposals only if real quote/license gates pass.
- [ ] **SL-33-15** Run independent safety drill for stale quote, feed outage, wrong expiry, open position at shutdown and crash recovery.
- [ ] **SL-33-16** Create complete strategy/model documentation, API schemas, SQL migrations, operation runbooks and developer handoff.
- [ ] **SL-33-17** Produce final artifact manifest of current code, datasets, active policies, evidence tier, net P&L, drawdowns and unresolved blockers.
- [ ] **SL-33-18** Define success even when results fail: deterministic research pipeline, measurable rejection, no unauthorized action and honest `NO_EDGE_FOUND`.
- [ ] **SL-33-19** Gate `LEARNING_PLATFORM_COMPLETE` on implementation and tests; gate `PAPER_EDGE_CONFIRMED` separately on fresh independent evidence.
- [ ] **SL-33-20** Declare all LIVE_CASH pathways closed until explicit human owner authorization, broker acceptance, licensing and independently reviewed risk.

**Wave acceptance:** A single command runs safe autonomous hypothesis research to evaluation on fixtures; deployment remains shadow/paper-only pending genuine evidence.


## SL-34. Independent forward-profit evidence and capital deployment rules — P0

**Ownership:** `profitability/release_gate`  
**Definition of outcome:** Judge realized outcomes after costs and quantify uncertainty, rather than optimising for a desirable headline number.

- [ ] **SL-34-01** Freeze strategy family, universe, benchmark, decision clock, capital, cost model, testing horizon and acceptable risk *before* future observations.
- [ ] **SL-34-02** Define monthly equity-momentum and 15-minute options evidence windows separately; overlapping trades or bars are not independent sessions.
- [ ] **SL-34-03** Count real observed net account equity by chronological evaluation period, including cash, idle days and current open-risk marks.
- [ ] **SL-34-04** Compute benchmark-adjusted return after fees/taxes where applicable and compare with simple matched-capital policies.
- [ ] **SL-34-05** Require independently reviewed or robustly reconstructed fill/cost evidence; disallow positive results based solely on synthetic proxy fills.
- [ ] **SL-34-06** Require dependence-aware confidence interval and appropriate null/multiple-testing gates from existing accepted research protocol.
- [ ] **SL-34-07** Conduct positive and negative control checks, regime splits, capacity stress and sensitivity to changing quote/cost assumptions.
- [ ] **SL-34-08** Assess profitability after honest drawdown, CVaR and risk-limit breach accounting; never trade through breached safety constraints.
- [ ] **SL-34-09** Compute evidence quality and sample sufficiency; return `INSUFFICIENT` rather than declaring profit from several lucky weeks.
- [ ] **SL-34-10** Require operational completeness: no unresolved position, duplicate order, stale-as-executable close, missing fee or unlicensed data used in claim.
- [ ] **SL-34-11** Perform independent review of repeated adaptive model revisions, online selection looks and re-registration to avoid double spending holdouts.
- [ ] **SL-34-12** Declare `NO_CONFIRMED_EDGE` when positive training reward fails prospective economic gates; the platform may still be engineering-complete.
- [ ] **SL-34-13** Preserve unchanged champion as baseline until a challenger wins a prospectively predeclared comparison.
- [ ] **SL-34-14** Measure incremental value from discovery, selection, position sizing, execution timing and risk separately with ablation cohorts.
- [ ] **SL-34-15** Create reporting that clearly separates historical hypothetical, fresh local paper, independently attested forward and real brokerage outcomes.
- [ ] **SL-34-16** Define real-money pilot limits by owner approval, legal permissions, broker constraints and explicit stop/rollback; no automatic capital scaling.
- [ ] **SL-34-17** Forbid transferring position-size gains from synthetic/low-cost conditions directly to real funded capital.
- [ ] **SL-34-18** Implement operational de-escalation to CASH and independent owner alert on live loss-limit, data or custody breach.
- [ ] **SL-34-19** Require a new evidence gate after each material strategy change; no perpetual profitable certification.
- [ ] **SL-34-20** Publish final signed review with explicit answer: what made money, when, relative to what, at what risk/cost, and how reliable is the evidence.

**Wave acceptance:** Profit gate returns PASS, FAIL or INSUFFICIENT_EVIDENCE reproducibly; only a separate later human-controlled process may consider real capital.



---

# Additional specifications for direct Codex implementation

## A. First-party module ownership map (proposed paths, not assertions they exist)

```text
src/financial_brain/learning/
  core/{contracts,clocks,policy_state,permissions,registry}.py
  data/{pit_store,feature_store,trajectories,behavior_support}.py
  simulator/{market_env,events,execution,markets,stress}.py
  reward/{economics,benchmark,risk_utility,reconcile}.py
  discovery/{grammar,dsl,canonicalize,mechanisms,propose}.py
  models/{baselines,supervised,calibrate,regimes}.py
  bandits/{linear_ucb,thompson,delayed_feedback,ope}.py
  offline_rl/{behavior_clone,fqi,fqe,cql,iql,policy_support}.py
  risk_rl/{distributional,cvar,constrained,safety_projector}.py
  world/{baseline_dynamics,probabilistic_rollout,uncertainty}.py
  hierarchy/{desk_router,policy_options,allocation}.py
  desk/{momentum,index15m,options,etf,mf,futures}.py
  eval/{chronology,ope,statistics,selection_bias,claim_tiers}.py
  agents/{planner,author,critic,executor,auditor,sandbox}.py
  ops/{shadow,learning_clock,retrain,promote,rollback,monitor}.py
  ui/{learning_console,scorecards,explanations}.py
  tests/  (optional package-local smoke/fixture tools)
tests/learning/{unit,integration,property,adversarial,science}/
docs/learning/{ADRS,EXPERIMENTS,PROTOCOLS,RESULTS,MODEL_CARDS}/
```

Adapters should reuse the existing real repository primitives, not copy them:

- `src/financial_brain/forecasting/{distribution,ensemble,calibration,walkforward,null}.py`
- `src/financial_brain/research/{preregistry,ensemble_walkforward}.py`
- `src/financial_brain/forward_edge/{session_runner,durable,market_data,evidence_stats,trials}.py`
- `src/financial_brain/execution/oms/{engine,journal,reconcile,shortfall,killswitch}.py`
- `src/financial_brain/kernel/{paper_account,ledger,units}.py`
- `src/financial_brain/paper/{intraday_runner,options_runner,zoo_edges,decision_journal}.py`
- `src/financial_brain/forecasting/index_15m.py` on the five-desk development branch **if reconciled and merged**.
- `src/financial_brain/evaluation/dynamic_standard.py` on the dynamic-standard branch **if adopted consistently with trial governance**.

## B. Typed policy and event contract (proposed, illustrative)

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Action = Literal['NO_TRADE', 'ENTER_LONG', 'ENTER_SHORT', 'REDUCE', 'EXIT', 'HOLD']
Tier = Literal['RESEARCH', 'REPLAY', 'SHADOW', 'LOCAL_PAPER', 'INDEPENDENT_FORWARD']

@dataclass(frozen=True)
class PolicyContext:
    policy_sha: str
    instrument_id: str
    available_at: datetime
    session_id: str
    portfolio_snapshot_sha: str
    feature_snapshot_sha: str
    entitlement_sha: str
    open_risk_inr: float

@dataclass(frozen=True)
class PolicyProposal:
    context: PolicyContext
    proposed_action: Action
    confidence: float | None
    expected_net_alpha: float | None
    uncertainty_interval: tuple[float, float] | None
    intended_quantity: int | None
    reason_codes: tuple[str, ...]
    evidence_tier: Tier

@dataclass(frozen=True)
class GovernorDecision:
    proposed_policy_sha: str
    final_action: Action
    approved_quantity: int
    reason_codes: tuple[str, ...]
    cash_orders_enabled: Literal[False] = False  # fixed paper-only; learned policy cannot enable
```

Use decimal currency and instrument-specific lots in production; `float` above only illustrates the learning-facing data shape. Every object is immutable after timestamping. Each proposed action and final governor decision needs a unique idempotency key; no free-text field can authorize a trade.

## C. Minimal data model (conceptual DDL)

```sql
CREATE TABLE learning_experiments (
 experiment_id TEXT PRIMARY KEY,
 family_id TEXT NOT NULL, parent_id TEXT,
 preregistration_sha TEXT NOT NULL,
 code_sha TEXT NOT NULL, train_data_sha TEXT NOT NULL,
 protocol_sha TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL,
 status TEXT NOT NULL
);
CREATE TABLE learning_policies (
 policy_sha TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
 feature_sha TEXT NOT NULL, reward_sha TEXT NOT NULL,
 weights_sha TEXT NOT NULL, governance_sha TEXT NOT NULL,
 stage TEXT NOT NULL, available_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE learning_decisions (
 decision_id TEXT PRIMARY KEY, policy_sha TEXT NOT NULL,
 session_id TEXT NOT NULL, instrument_id TEXT NOT NULL,
 event_available_at TIMESTAMPTZ NOT NULL,
 decision_at TIMESTAMPTZ NOT NULL,
 proposed_action TEXT NOT NULL, approved_action TEXT NOT NULL,
 source_sha TEXT NOT NULL, risk_gate_sha TEXT NOT NULL,
 evidence_tier TEXT NOT NULL
);
CREATE TABLE learning_outcomes (
 decision_id TEXT NOT NULL, evaluation_clock TIMESTAMPTZ NOT NULL,
 ledger_event_sha TEXT, economic_status TEXT NOT NULL,
 realized_net_return DOUBLE, matched_benchmark_return DOUBLE,
 attribution_sha TEXT NOT NULL,
 PRIMARY KEY (decision_id, evaluation_clock)
);
CREATE TABLE policy_reviews (
 review_id TEXT PRIMARY KEY, candidate_sha TEXT NOT NULL,
 champion_sha TEXT, evidence_sha TEXT NOT NULL,
 verdict TEXT NOT NULL, reviewer TEXT NOT NULL,
 reviewed_at TIMESTAMPTZ NOT NULL
);
```

The above is a **proposed teaching schema**, not a replacement for `kernel/ledger.py` or the canonical paper OMS. Real deployment requires FK relations, event sequencing, account ID, fees, full state snapshots, constraints and migrations.

## D. Deterministic learning and research loop (pseudocode)

```python
for observed_event in permitted_market_event_stream:
    append_immutable_observation(observed_event)
    if not as_of_and_entitlement_preflight(observed_event):
        record_abstention('SOURCE_OR_CLOCK_BLOCK')
        continue

    feature = causal_feature_store.update(observed_event)
    risk_state = canonical_portfolio_ledger.snapshot()
    proposal = frozen_champion.propose(feature, risk_state)
    decision = independent_risk_governor.project(proposal, risk_state)
    journal.record_before_any_execution(decision)

    # Safe local shadow evaluation only; never write to a physical broker.
    shadow_cohort.record(decision, observed_event)
    settle_only_legitimate_observed_outcomes()

    # The candidate is always a separate version. It does not silently
    # replace the champion or alter previously submitted positions.
    if newly_settled_outcomes_available():
        challenger_trainer.append_asof_outcomes()
        if frozen_review_window_reached():
            comparison = independent_evaluator.compare_frozen_policies()
            registry.record_review(comparison)  # never auto-arm real orders
```

**Nightly/periodic researcher loop** (only after matching trial-budget authorization): hypothesis DSL → static lint and existence check → provenance/entitlement check → cost preflight → preregistration → train/validation → independent falsification → sealed OOS → shadow candidate if eligible. Invalid or already tested hypotheses enter failure memory, not a restart of protected testing.

## E. Mandatory experiment groups (smallest legitimate sequence)

1. **Correctness control:** randomized price with bid/ask and costs. Expected result: no reliable positive alpha on properly guarded large-sample forward evaluation.
2. **Positive control:** known causal synthetic reward environment with known support and fill mechanics. Expected result: algorithm recovers the correct action within registered tolerance; synthetic outcome is not market proof.
3. **H005/H003/P3 bandit:** freeze candidate book rules, compare fixed best, equal risk and CASH to contextual selector on chronological held-out and new paper months.
4. **Nifty 15-minute forecast:** compare distributional forecasts against random-walk/calibrated frequency baselines. Judge proper probability scoring before testing contract-level options decisions.
5. **Options conversion:** exact bid/ask CE/PE and defined-risk spread evaluation on permissible evidence; test correct direction but negative option premium as a **loss**.
6. **Conservative RL:** after simpler baselines are measured, compare behavior cloning, FQI/FQE, CQL/IQL-style constrained policies with known action support.
7. **Execution RL:** select patient/marketable order tactics for frozen identical parent intents; judge realized shortfall and completion, not extra price-trading alpha.
8. **Autonomous researcher:** seed 100 null hypotheses with many variants and require proper selection correction; measure rate of false approvals and correct refusals.
9. **Forward contest:** register champion/challenger, freeze risk/versions and allow actual sessions to occur; report all days and trades including cash, rejects and unresolved exits.
10. **Independent proof:** compute economic audit on frozen cohorts and output PASS/FAIL/INSUFFICIENT without editing prior protocol.

## F. What must be tested by design, not by selecting favorable market data

- Historical past decisions remain identical when future features, adjusted prices, labels, revisions and option expiries are mutated.
- Offline RL Q-values cannot create fictitious high-reward actions outside behavior policy support.
- More than a thousand trials of purely random signal families are penalized under the pre-registered multiplicity policy; false candidates do not appear reliably profitable.
- Every trade and cash line reconciles to the same economic P&L; cost events never double-count or disappear.
- Learner cannot raise drawdown cap or change the initial capital to manufacture good risk-adjusted results.
- Positive direction forecasts with negative executable option premia produce losses, not wins.
- Missing or stale bids do not yield zero-slippage marketable fills.
- A missing close never becomes a fictional profitable exit after a restart.
- Every new learned checkpoint creates a new strategy version and a new research-selection event.
- Any open/unknown positions, accounting mismatch, unlicensed source or unauthorized broker action blocks promotion.
- A de-risking or kill-switch event can occur without the learner process or GPU running.
- A complete production-quality research platform can finish with `NO_EDGE_FOUND`; never override the result.

## G. Suggested independent research reference reading

These are methodological references and should not be mistaken for proof of trading profitability or evidence that the algorithms work on Indian markets.

1. Kumar et al. (2020), *Conservative Q-Learning for Offline Reinforcement Learning*: https://proceedings.neurips.cc/paper/2020/hash/0d2b2061826a5df3221116a5085a6052-Abstract.html
2. Kostrikov, Nair & Levine (2021), *Offline Reinforcement Learning with Implicit Q-Learning*: https://arxiv.org/abs/2110.06169
3. Dudík, Langford & Li (2011), *Doubly Robust Policy Evaluation and Learning*: https://arxiv.org/abs/1103.4601
4. Su et al. (2020), *Doubly Robust Off-policy Evaluation with Shrinkage*: https://proceedings.mlr.press/v119/su20a.html
5. Laroche et al. (2019), *Safe Policy Improvement with Baseline Bootstrapping*: https://proceedings.mlr.press/v97/laroche19a.html
6. Chow et al. (2015), *Risk-Sensitive and Robust Decision-Making: a CVaR Optimization Approach*: https://proceedings.neurips.cc/paper_files/paper/2015/hash/64223ccf70bbb65a3a4aceac37e21016-Abstract.html
7. Bailey et al., *The Probability of Backtest Overfitting*: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253
8. Bailey & López de Prado, *Deflated Sharpe Ratio*: https://doi.org/10.2139/ssrn.2460551
9. Chen et al. (2021), *Decision Transformer*: https://proceedings.nips.cc/paper_files/paper/2021/hash/7f489f642a0ddb10272b5c31057f0663-Abstract.html

## H. Final engineering and business success contracts

### Engineering-complete

A single `fb learn` pathway can run every stage end-to-end, with real source entitlements enforced, deterministic independent evaluation, accurate market costs, causal policy versions, offline training, audited agent proposal/rejection, replay/shadow orchestration, crash restoration, independent risk governor, no unauthorized broker calls, preserved failures and reproducible results.

### Edge-confirmed

Only new, prospective, independent observations meeting **pre-registered** benchmark, net economics, confidence, capacity, implementation, source permission and risk criteria justify labeling an edge confirmed. A historically positive RL backtest or AI-generated promising setup alone never suffices.

### Real-money ready

Requires an additional owner-governed release and legal/broker/operational process, not a checkbox in this file. Never equate in-house code ownership with ownership of market data or permission to execute financial orders.

> **No guarantee of profit.** The desired outcome is a self-improving, scientifically honest platform that can identify, safely exploit when approved, or reject an economic edge. The money-making claim remains earned by independent market evidence, not by optimization of an artificial reward.
