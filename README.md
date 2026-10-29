# Financial-Brain

Inverse-policy research and its limits are documented in
[docs/INVERSE-STRATEGY-IMPLEMENTATION.md](docs/INVERSE-STRATEGY-IMPLEMENTATION.md).
The read-only specialist coordinator and its limits are documented in
[docs/FINANCIAL-BRAIN-AGENTS.md](docs/FINANCIAL-BRAIN-AGENTS.md).
The shared read-only CLI/MCP contract is documented in
[docs/MCP-CLI-CONTRACT.md](docs/MCP-CLI-CONTRACT.md).
The current implementation-versus-roadmap audit is in
[docs/ROADMAP-AUDIT-2026-10-29.md](docs/ROADMAP-AUDIT-2026-10-29.md).
The [Trader Core v1 audit](docs/TRADER-CORE-V1-AUDIT.md) maps the nine product requirements
to current code and unverified acceptance gates; `fb trader-core` provides a read-only
integrated status of the saved research snapshot.
The [Rust, US-linkage and options plan](docs/RUST-US-OPTIONS-PLAN.md) records the
benchmark-first language decision and data/evidence requirements; `fb options-audit`
reports the local derivatives-data coverage without treating candles as executable quotes.
The [fresh trader-platform checklist audit](docs/TRADER-PLATFORM-CHECKLIST-AUDIT-2026-10-29.md)
tracks each immediate, next and advanced requirement against source and remaining evidence.
The [engineering integrity checkpoint](docs/ENGINEERING-INTEGRITY-2026-10-29.md)
records the cross-platform DuckDB CI failure, the local fix, coverage baseline,
champion attestation limits, and the economic-alpha/utility contract change.

**An empirical, AI-native market decision engine for Indian markets.**

Given only the information available at a moment in time, it looks for the best available opportunity across the
whole liquid market, estimates the distribution of outcomes, states the **expected net alpha** (expected return
minus benchmark, fees, spread, impact and a risk penalty), allocates risk, executes on paper under realistic
costs, and learns from both the trades it took and the opportunities it rejected.

> LLM = reasoning and orchestration. Point-in-time market data, exchange XBRL fundamentals, filings and the
> measured evidence = source of truth.
> `Data → Evidence → Opportunity → Expected net alpha → Risk → Decision → Execution → Outcome → Learning`

What sets it apart from systems where *an AI produces a signal*: every signal here arrives with its empirical
evidence, its point-in-time validity, a historical base rate from comparable situations, a forecast distribution,
its net alpha after costs, its place in the portfolio, a realistic execution and a counterfactual outcome - and
every test is written down before it is run (`fb preregs` audits that against git).

**Status:** Phases 0–3 complete; **Phase 4 - live paper testing - started 2026-10-05**.
Every trading day the system now scans the market, posts a 09:30 board, paper-trades its intraday rules
through the session and scores itself against everything it passed over. See [The daily loop](#the-daily-loop).

The system can research, decide, size, trade on paper and attribute its own P&L. What it
**cannot yet do is demonstrate a tradeable edge**, and the most useful thing in this
repository is now the record of how that was established rather than assumed. See
[What has actually been validated](#what-has-actually-been-validated).

* **Phase 0 — foundations.** `fb gate` passes all 12 bases on **2015–2026**: 2,903
  sessions of NSE + BSE prices (16M universe rows, 17,060 ISINs), 228 indices, 895
  corporate actions including ISIN successions, every price gap triaged.
* **Phase 1 — perception.** BSE announcements classified by deterministic rules
  (macro-F1 0.99), market-regime brain, evidence ledger, world state, and a daily brief
  in which **every claim carries a citation to the bytes it came from**.
* **Phase 2 — research and committee.** Investment committee with bull/bear debate over a
  cited dossier; immutable decision records; an Investment Constitution that can block a
  draft; the promoter/group knowledge graph; a model router that picks models by
  measurement (ADR-0002) and **declines when nothing clears its calibrated bar**; and
  invalidation conditions that are re-checked nightly and reported in the brief.

* **Phase 3 — validation.** Point-in-time paper engine with a measured drift floor, P&L
  attribution that closes as an identity, opportunity memory over every candidate
  *considered* (not only traded), distributional forecasting with a block-bootstrap null,
  and cross-sectional replication testing. This phase has destroyed more results than it
  has produced, which is the point.

Next: see [docs/HANDOFF.md](docs/HANDOFF.md) and [docs/BUILD-FLOW.md](docs/BUILD-FLOW.md).

## What has actually been validated

**2026-10-13, under one bar ([ADR-0004](docs/adr/0004-one-evidence-standard.md)):** the cost-aware momentum book
(momentum + 52-week high, 40-session hold, no-trade band) is positive net in both halves (+1.36% / +0.75% per 40
sessions) and beats an equal-turnover random-book null by a distance, but t is 1.45 / 1.24 - **not an edge yet**; it
is being forward-tested. Futures short build-up fails. The order management system exists
([ADR-0005](docs/adr/0005-broker-path-behind-stages.md), [runbook](docs/RUNBOOK-LIVE.md)) and is locked: nothing can
reach the broker until a strategy is confirmed out of sample and the owner arms a stage.

**Added since 2026-10-03, each pre-registered:** costs beat the edge for everything except slow momentum (a delivery
round trip is ~0.24-0.45%, 84% of the fixed part is STT); tax takes ~20% of an active strategy's CAGR; index timing
rules halve drawdowns but none beats buy-and-hold; stops neither help nor hurt; swing and opening-range setups lose
after costs; a registered *median* benchmark manufactured a swing edge until the mean was used (recorded against
myself); first point-in-time fundamentals test is mostly null; second-order promoter spillover is null.

Written down because an impressive architecture is not evidence, and this repository's own
measurements are the only thing that is.

**Replicates across independent size tiers, and survives a held-out cross-section**
(ranks 501–1500, never used in selection, predictions registered in git before running):

| signal | direction | held-out t | note |
|---|---|---|---|
| `vol_60`, `atr_pctile_250` | low volatility wins | −28.4, −23.1 | one idea, measured twice |
| `mom_12_1`, `dist_52w_high` | high momentum wins | +24.3, +17.5 | one idea, measured twice |
| 4 candle patterns | as a chart trader would say | −10.7 to +4.0 | bearish engulfing bearish, doji nothing |
| `WOBV_D20` | volume-weighted reversal | −32.8 | raw `ret_20d` does **not** replicate; weighted by volume it does |

**Does not survive:** a rebalance horizon swept on P&L (+246% in sample, **−7.49%** out);
an event-exclusion filter (+82.9 points in sample, **−4.53** out); three volume-flow
indicators claimed as a new family (all three flip sign out of sample); Kronos as a
distributional forecaster (**loses 44% of CRPS to a random walk**, intervals half the
width they need); and 47 of 113 technical indicators, which flip sign between tiers.

**The uncomfortable results, kept because they are the honest ones:**

* **Naive momentum beats everything built on top of it.** `ORDER BY mom_12_1 DESC` at a
  60-session hold returns **+15.40% mean out-of-sample excess, positive in 3 of 3 folds**.
  No signal, filter or horizon selection has improved on it out of sample.
* **All four surviving signals reverse together in a crisis.** Momentum IC goes +0.056 →
  **−0.132**; low volatility stops winning and reverses. They are not complements — a book
  holding both is doubly exposed to one regime.
* **Costs decide everything.** Gross, the signal is worth +151 points over its universe in
  eleven years; net at a 20-session rebalance it is −135. The crossing is near h60.

**Method that works, after everything else failed:** register the prediction, commit it,
*then* test on data that had no part in the selection. Two walk-forwards and a baseline
comparison killed every result they touched; pre-registered holdouts have passed 9 of 11.
The ordering was the problem.

```bash
pip install -e .
fb migrate
fb prefetch --start 2015-01-01 --end 2026-09-18     # parallel, network only
fb ingest --start 2015-01-01 --end 2026-09-18 --from-lake
fb reference && fb index --start 2015-01-01 --end 2026-09-18
fb derive --rebuild && fb gaps --auto-review --redo && fb gate
```

Day to day, one command does the cycle — prices, announcements, regime, features, NAVs,
news references, tone, invalidation checks and the brief — and each step reports
separately, so a missing corporate-action month never costs the morning brief:

```bash
fb daily
```

The pieces it runs, usable on their own:

| Command | What it does |
|---|---|
| `fb brief --date D` | The daily brief for the window D 09:00 → next session 09:00, every claim cited |
| `fb tone --date D` | Shareholder tone of the day's material filings, via the model router |
| `fb newsref --date D` | Recovers the news headline a filing quotes, from the filing's own text |
| `fb newsfetch --date D` | Fetches a filing's linked article where the publisher's robots.txt allows it |
| `fb fundamentals SYM…` / `--watchlist` | Screener ratios (Tier 3), cross-checked against our own close |
| `fb mfnav` | AMFI's public daily NAV feed (~14k schemes, with ISINs) |
| `fb committee --isin X` | Investment committee: stances, bull/bear debate, chair's draft |
| `fb monitor [--dry-run]` | Re-checks live decisions' invalidation conditions |
| `fb models bench/route/plan` | Measure models on labelled Indian data; choose and verify a route |
| `fb security` | Untrusted text that tried to steer a model, and what was done about it |
| `fb trace <evidence_id>` | Open any claim down to the source bytes |

Validation modules, used from Python rather than the CLI:

| Module | What it answers |
|---|---|
| `evaluation/replication.py` | Does an edge hold in independently measured size tiers? Four tiers at abs(t) >= 2 with one sign is p = 5.4e-07 — 370× stronger than clearing a single-test Bonferroni bar, and it is what makes a 113-candidate search affordable |
| `evaluation/economic.py` | Does it beat naive momentum, value, low-vol and the index — and does a horizon survive being chosen on training data only? |
| `evaluation/events.py` | Does a filing or candle pattern move prices beyond what momentum and the last month's return already explain? |
| `evaluation/sample_bias.py` | Is a candidate generator's sample representative of the opportunity set it generalises to? |
| `paper/engine.py` | Walk-forward paper trading with `calibrate()` — the engine's own drift floor, because an excess smaller than it means nothing |
| `attribution/pnl.py` | Market vs universe selection vs stock selection vs costs, as a log identity that closes to 1e-14 |
| `opportunity/memory.py` | Every candidate considered, why it was rejected, and what it then did |
| `forecasting/` | Distributional forecasts, a stationary block-bootstrap null, calibration, and Kronos behind the same interface as a bootstrap |
| `risk/sizing.py` | Cornish-Fisher quantiles, EVT tail fit, regime-mixture Kelly, conditional drawdown at risk |
| `opportunity/engine.py` | The whole liquid market ranked by net expected alpha after sized costs: TRADE / WATCH / REJECT with a reason, every decision written to memory. At the assumed IC it trades almost never, which is the honest answer |
| `opportunity/replay.py` | Walks the engine through history and checks what it claimed: calibration slope 0.71, composite IC +0.083 (t 5.5), top-20 book net negative — the edge is in *avoiding the bottom two deciles*, not picking the top |
| `paper/daily.py` / `fb paper-daily` | The daily loop: scan, record, resolve what matured. No order code exists in its reach; live decisions are the only evidence made before their outcomes |
| `risk/allocator.py` | Capital to strategy sleeves by decayed, shrunk edge (PAPER / ACTIVE / REDUCE / STOP); nothing gets real capital unless its money-gate flag is set |
| `evaluation/event_response.py` | 606k filings with their price response, and "have we seen this before?" analogues using only closed outcome windows |
| `features/intraday.py` | First-hour features from minute bars (300 names, 55M bars). Only `range_1h` has a directional IC, and its tails earn 0.5 bp against 14-54 bp of cost |
| `ingest/ticks.py` | Live ticks into the same minute bars (cumulative-volume differencing, late ticks dropped); the streaming connection is the owner's to open |
| `ingest/xbrl_results.py` / `providers/nse_results.py` / `ingest/integrated_filing.py` | Point-in-time fundamentals from the exchange's own XBRL, including the Integrated Filing feed from 2025 onward; dissemination time, revisions and arithmetic checks are retained |
| `evaluation/half_life.py` | How long a signal's information lasts: reversal halves in 16 sessions; momentum, 52-week-high and low-vol are still accruing at 120 |
| `evaluation/spillover.py` | Does a results reaction pass through promoter siblings? No: a clean null at 1, 5 and 20 sessions |
| `risk/killswitch.py` / `risk/degradation.py` | Drawdown, daily-loss, stale-data, degraded-edge and cost-overrun trips that halt the paper loop; only a human resets it. CUSUM + rolling-t detector on the IC |
| `portfolio/optimizer.py` / `attribution/pipeline.py` | Ledoit-Wolf, turnover-aware long-only weights; market / signal / sizing / cost / execution layers that sum exactly, plus the forecast error |
| `ingest/kite_fno.py` / `ingest/kite_live.py` | Open interest, PCR, max pain, implied vol and skew; live quote snapshots checked against the archive. Read-only |
| `research/retrieval.py` / `regime/breadth.py` / `execution/ticket.py` | Cited point-in-time retrieval; breadth and empirical sectors; a whole-share small-account sizer and a ticket marked "NOT SENT ANYWHERE" |
| `decisions/plan.py` / `fb plan TICKER --style position\|swing\|intraday` | Position size (risk budget over stop distance, capped by weight and liquidity), time horizon and exits for a name, with the *evidence for the style* stated first: position WEAK_IN_SAMPLE, swing and intraday NONE_DEMONSTRATED. Information, not advice; nothing is sent to a broker |
| `evaluation/swing.py` / `evaluation/orb.py` | Swing setups and an opening-range breakout with stops, after costs. Swing: vs a random liquid name the gross edge is -0.15% to +0.21% (not significant) and every setup loses 0.2-0.6% a trade net. ORB: gross ~0, net -15 to -21 bp over 251k trades. A registered *median* benchmark manufactured a swing edge until the mean was used |
| `evaluation/market_timing.py` / `evaluation/exit_rules.py` | Index timing rules (none beat buy-and-hold; trend/breadth/vol roughly halve the drawdown at a CAGR cost) and stop rules (nearly free, nearly useless) |
| `costs/tax.py` / `costs/calculator.py` / `fb costs-calc --order N [--ticker T] [--gain 0.03] [--table]` | Itemised rupee cost of a trade (exact charges, impact as a range), break-even, cost per session held, and the tax on a hypothetical gain (STCG 20.8%, LTCG 13% over Rs 1.25 lakh, intraday at slab) with loss set-off. Rates are parameters; information, not tax advice |
| `evaluation/after_tax.py` | The same return series taxed as an active book (all gains realised yearly) and as buy-and-hold (deferred): tax takes about 20% of an active sleeve's CAGR, deferral is worth 2-3 points a year |
| `evaluation/news_model.py` | Headline classifier on filings: text lowered strength AUC and direction missed the bar |
| `opportunity/scanners.py` | Ten named scanners (momentum, relative strength, low volatility, composite, mean reversion, breakout, volume anomaly, volatility expansion, event, fundamental value), each with its evidence status and horizon. Expected return comes from **one** model - what the name's composite decile earned in the replay, scaled by the half-life curve - so scanners say *why* a name is interesting but cannot each invent a return |
| `paper/trader_view.py` (on the terminal) | **What should I trade today?** The top 10 by expected net alpha, each answering: why this (rank of ~1,500 and what lifted it), why now (a checklist with cautions), the edge (signal, analogues, fundamentals, regime, F&O, liquidity - each in words), the itemised net alpha, if I'm wrong (P(up), gain if right, loss if wrong, reward/risk), invalidation, **how much** (an in-page calculator on your capital and risk), how to enter (band, don't-chase level, slippage), the case against, and what was watched but not listed |
| `providers/kite_account.py` / `portfolio/my_book.py` / `fb my-portfolio --sync` | **Your own Kite account, read only** (holdings, positions, trade book; three exact paths, GET only, stored in `data/`): since cost, thesis health by rules (EXIT BY RULE only on the plan's tested exits), book volatility, beta, share at risk to the stops, sector concentration, and which of today's names add the least risk for their alpha |
| `portfolio/journal.py` / `fb journal` | Your round trips (FIFO) with an autopsy each - market, sector, selection, entry and exit against the day's close, costs - and a report on strengths and weaknesses once 30 trips exist |
| `alerts.py` / `fb alerts` | Windows desktop alerts, sent once each: a holding exiting by rule, a name clearing +1% net, a halted loop, a live run that did not complete |
| `signals/watch.py` | Market-hours research watcher: fresh Kite quotes, bootstrap baseline observations, two-sided option scenarios and holding stop reviews. Unconfirmed stocks are explicitly labelled **research watch — no trade signal**; option scenarios are logged without push alerts. No order is sent |
| `features/fundamentals_pit.py` | 295k point-in-time observations from exchange XBRL (revisions numbered, never overwritten; `as_known_on` a date): EBITDA, EBIT, debt, cash, book value, cash flow, and P/E, P/B, EV/EBITDA, ROE, ROCE, debt/equity, interest cover at a price - labelled stale where the results are |
| `evaluation/half_life_table.py` | Every signal at every horizon as a trader reads it: excess, hit rate, Sharpe, MFE/MAE, turnover, cost drag, net. Momentum is the only signal that pays long-only at every horizon (+1.50% net over 60 sessions); low volatility ranks well but its top quintile under-earns |
| `opportunity/lifecycle.py` / `fb lifecycle TICKER --day D` | One opportunity end to end: found, taken or rejected (and why), sold, resolved. Every opportunity is typed: direction, evidence ids, an experiment id (pre-registration + a hash of the pricing model), an outcome distribution |
| `features/library.py` / `evaluation/deep_library.py` / `evaluation/learned_model.py` | **The deep feature library**: 161 features as of each close (no look-ahead, tested) - fair value gaps, liquidity sweeps, structure, order blocks, ~15 candles, Fibonacci (retracements, golden pocket, extensions, Fibonacci MAs), pivots, Ichimoku, SAR, Supertrend, Heikin-Ashi, 30+ named oscillators, volume profile, anchored VWAP, Gann, Wyckoff, harmonics. Every feature tested alone under a 5% false-discovery rate across the library, in 4 liquidity tiers and held out: 26 count, all versions of volatility, reversal, liquidity or momentum; no FVG, Fibonacci-level, Gann, harmonic or candle effect counts as named. Bullish FVG retests underperform; bearish ones keep lagging (t -10, to be tested as an avoid-filter). A walk-forward LightGBM over all of them earned +0.77% a month in 2022-26 (t 2.3), 0.29% ahead of momentum (not significant) - a live challenger |
| `decisions/opportunity_card.py` / `portfolio/next_capital.py` / `decisions/exit_brain.py` / `evaluation/regret.py` / `evaluation/evidence_tiers.py` / `ingest/quote_archive.py` | **The trader core.** The full Opportunity (regimes, catalysts, technical/fundamental/event/derivative state, distribution, P(profit), MFE/MAE, separate economic net alpha and risk-adjusted portfolio utility, capacity, size, execution plan, provenance; exported daily to `data/terminal/opportunities.json`); where the next Rs 1 lakh of risk goes (with cash as an answer) and the alternatives table; the exit brain's six views per holding (EXIT only on tested rules); selection regret for the books and "you chose X, the list's best was Y" for your own trades; evidence shown by kind - historical, walk-forward, paper-forward, live-forward - with confidence from the forward tiers only; every live poll archived (bid/ask, depth, VWAP, OI) and rebuilt into 1-60 minute bars; VWAP, book-walk cost and shortfall recorded beside each paper fill; the funnel from every traded name to the few that improve your book |
| `paper/zoo.py` / `fb zoo` and the learned, momentum and v2 books | **Everything that was tested now trades on paper every evening, rules frozen as tested**: 11 technical rules, pairs (distance, cointegration), PCA stat-arb, VIX timing, scale-trader, grid, martingale and anti-martingale (the zoo), and five books - position (top 5), swing (top 50), the learned model, the momentum champion and ranking v2 (top 20 each). Every learned-model buy records the combination behind it (the model's per-feature contributions, with level and direction). The day's results arrive at 18:30 (`fb daily-results`) |
| `evaluation/basis.py` / the zoo's `carry_basis_10` | **Cash-and-carry on 219 F&O names, 2016-2026** (Kite continuous futures, read only): unconditionally the futures pay 4.9% a year, below the risk-free rate (net 0.7%). But when a future trades more than 10% a year above spot, buying the stock and selling the future to expiry has beaten the risk-free rate after costs - +0.14% a contract, t 2.9 by month (5.2 in 2022-26), worst contract -2%. The first arbitrage result here that survives costs; now in the live paper zoo |
| `decisions/net_alpha.py` | **Economic net alpha, itemised** - expected excess return less fees, spread and impact; the variance penalty is reported separately as risk-adjusted utility. Scanner ranking uses economic net alpha, which remains an unvalidated model estimate. |
| `evaluation/neutral.py` | Signals with market beta, home sector, size (turnover proxy) and volatility removed: momentum keeps 94%, 52-week high 91%, the composite 102% (t 4.3) - stock selection, not disguised factor bets |
| `research/preregistry.py` / `fb preregs` | Audits every pre-registration against git: committed before its result, never edited after it (19 audited, 0 violations; one amendment, made before any result) |
| `paper/execution.py` | Each intraday strategy's P&L split into what the signal earned at its trigger prices, what execution cost, and the charges |
| `paper/reconcile.py` / `fb paper-compare` | Read-only, exact-day/strategy/symbol/side comparison of closed live and replay paper trades; refuses to infer an execution gap from different days |
| `evaluation/prospective.py` / `fb champion-audit` | Future champion picks get write-time attestation and the displayed standings exclude historical/unattested picks; the audit is read-only and existing picks cannot be retroactively certified |
| `paper/loss_review.py` *with-market view* | The owner's rule - trade only with the market - tracked on the real trades every day as a labelled shadow (it failed its held-out test) |
| `paper/loss_review.py` / `fb loss-review [--propose]` | **Learning from the bad trades.** Every intraday paper trade records its decision-time context (market move since the open, daily trend, gap, opening-range width, minute); the review finds the situations that keep losing, one observation per day, with the chance count beside them, and writes them as PROPOSED hypotheses. Nothing changes until a hypothesis is pre-registered and survives data it was not found on. First test of the loop - *trade only with the market trend* - doubled ORB's gross in the training year and added nothing in the held-out year (net -21 to -25 bp); it does not qualify |
| `paper/book.py` / `fb book [--book swing-v1\|book-v1] [--replay START --label L] [--report]` | **Paper books for the suggestions**, each pre-registered: *swing-v1* buys the top 50 swing candidates each evening (2% each, at most 50 open, swing stop, sold after 7 sessions), *book-v1* the top 5 position names (60 sessions). Bought at the next close, sold at the stop or the horizon, net of the sized round trip, against the universe bought and held over the same days. Every trade is kept and the terminal shows the worst beside the best. Replays are labelled in-sample; the live record is the evidence (verdict after 100 trades over 20 entry days). book-v1's replay since 2023: +0.79% a trade net but **-3.1% against the universe** (t -2.5) - a large-cap tilt through a small-cap rally |
| `paper/terminal.py` / `fb terminal` | One HTML page: market and breadth, kill switch, top opportunities, paper trades, what changed (filings, escaped), strategy health and champion standings, and a drill-down per name (why now, evidence, analogues, range, net alpha, size, stop, time exit, execution). Written to `data/terminal/index.html` |
| `paper/decision_journal.py` | Owner-entered, append-only record of prospective choices and decision latency; descriptive workflow evaluation only, with no order path. See [trader decision workflow](docs/TRADER-DECISION-WORKFLOW.md) |
| `evaluation/script_mechanisms.py` | Read-only, training-only MQL5/Pine-inspired mechanism diagnostics: regime breakout, compression breakout and a visible-depth no-trade filter. No imported source code, trial verdict or live signal; see [edge development](docs/EDGE-DEVELOPMENT.md) |
| `evaluation/bounded_search.py` | Reproducible 2016–2021 random search over 81 comparable daily-stock entry variants on a historical F&O stock sample; reports the training-selected rule against an equal-turnover cohort and internal validation, never a trial verdict or trade alert |
| `evaluation/setup_analogues.py` | The 200 most similar past situations by eight ranked features, shown beside the whole pool. Out of sample its 5-day IC is +0.053 against momentum's +0.009 (t 3.1, under the bar), probably short-term reversal; descriptive only |
| `evaluation/earnings_reaction.py` | Results reaction by surprise: day 0 top-minus-bottom quintile +1.5% (t 6.4), nothing after - no post-earnings drift to trade |
| `evaluation/intraday_half_life.py` | Daily signals inside the next day: the composite's IC from 09:30 to 15:15 is +0.046 (t 6.9), carried by low volatility and the 52-week high. Not a strategy (costs); a reason not to wait for a dip on entry |
| `evaluation/champion.py` / `fb champion --record` | Champion (naive momentum) against challengers, picks locked from 2026-10-05 and resolved only after their horizon; promotion needs 12 non-overlapping cohorts, t >= 2, after costs |
| `risk/health.py` / `portfolio/marginal.py` | HEALTHY / WARNING / REDUCE_CAPITAL / PAPER_ONLY / DISABLED from a strategy's own record, with day-clustered errors and 20 days before any verdict; the added risk of each candidate given the book |
| `opportunity/learning.py` | Which rejected groups later won: cost-rejected small high-momentum names beat the universe gross (+0.5-0.7%/mo), event blocks avoided losers (-1.5%/mo). Hypotheses for a new rule version, with the chance count beside them |
| `graph/exposure.py` | Measured exposure per name (market beta, home sector, rates, VIX, residual peers) and what-if propagation of a shock. Simultaneous co-movement, not a forecast |
| `ingest/kite_fno.py` / `fb fno-snapshot` | Futures basis and OI build-up (long build-up, short covering...), option-chain statistics and IV percentile, snapshot nightly so positioning changes day over day |

## Documentation

| Document | What it holds |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Design decisions, data and decision contracts, the four brains, agent model, validation gates, build order, the 15 non-negotiable rules |
| [docs/RESOURCES.md](docs/RESOURCES.md) | Every external resource — repos, data sources, models, datasets, benchmarks — with licence, role and a single verdict (INTEGRATE / WRAP / STUDY / REFERENCE / BUILD / REJECT) |
| [docs/FEASIBILITY-INDIA.md](docs/FEASIBILITY-INDIA.md) | Layer-by-layer feasibility scorecard, the five real blockers, what to cut or defer, India-adjusted build order |
| [docs/BUILD-FLOW.md](docs/BUILD-FLOW.md) | What must be built in what order, the resources each piece needs, critical path, milestones |
| [docs/PHASE-1.md](docs/PHASE-1.md) | Phase 1 (perception) progress: BSE corporate-action feed, derivation measured against it |
| [docs/adr/0002-tiered-models.md](docs/adr/0002-tiered-models.md) | Why models are chosen by measurement, and how a route is calibrated and verified |
| [docs/adr/0003-untrusted-text-and-prompt-versioning.md](docs/adr/0003-untrusted-text-and-prompt-versioning.md) | Treating fetched text as data, not instructions; why a calibration belongs to a prompt |
| [docs/PHASE-0.md](docs/PHASE-0.md) | What Phase 0 delivered, what the first real backfill discovered, and the gaps carried forward |
| [docs/adr/0004-one-evidence-standard.md](docs/adr/0004-one-evidence-standard.md) | The one bar every strategy passes, and the 8-trials-a-quarter budget |
| [docs/adr/0005-broker-path-behind-stages.md](docs/adr/0005-broker-path-behind-stages.md) | The order path, its five locks, and the rollout stages |
| [docs/RUNBOOK-LIVE.md](docs/RUNBOOK-LIVE.md) | Operating the OMS: before stage 1, daily, and when something goes wrong |
| [docs/adr/](docs/adr/) | Architecture decision records |
| [brain/](brain/) | Obsidian vault — the same material as a linked graph. Open `brain/` as a vault; start at `00-Meta/Home.md` |

## Four modes, one brain

The same data, evidence, risk and memory layers serve four different jobs. What changes between them is the
universe, the latency, the holding period, the costs that matter and - above all - **how a result is judged**,
so a five-minute signal is never evaluated with a twenty-day framework or the other way round.

| Mode | Holding period | Universe and data | Judged by | Evidence today |
|---|---|---|---|---|
| **Investor** | months to years | point-in-time prices, XBRL fundamentals, filings, promoter graph | after-tax return against holding the index, with deferral | Fundamentals faint in both samples (earnings yield positive but never significant; earnings surprise strongest in smaller names, t 3.4, under the bar). Buy-and-hold's tax deferral is worth 2-3 points a year to beat |
| **Position / quant** | 1-6 months | full liquid universe (~1,500 names), daily features | net alpha over the *same* universe after costs and tax, replicated across size tiers | Momentum and 52-week-high: about +3 points a year over the same universe after cost and tax, **in-sample**. Information still accruing at 120 sessions |
| **Swing** | 5-10 sessions | daily setups on liquid names | net return over a *random* liquid name (the mean, not the median) | **None.** Pullback, oversold, breakout and strength setups lose 0.2-0.6% a trade net |
| **Intraday** | minutes to the close | Kite minute bars and live quotes, 200-300 names | net bp a trade after the intraday schedule | **None.** Opening-range breakout: gross ~0, net -15 to -21 bp over 251,000 trades. Now being measured live |

The existing `data/minute_history.duckdb` Kite archive reached 2026-10-15 when checked on 2026-10-24.
After the owner completes that day's Kite login, `scripts\kite_history_refresh.cmd` refreshes the last 45 calendar
days for each year's top 300 liquid names. For a different bounded period use
`fb kite-history minute --top 300 --start YYYY-MM-DD`. Both paths refuse a missing session before opening the
writable history database and return a failure status if any symbol pull fails. The archive is historical candles,
not historical executable bid/ask; no option-fill claim should be inferred from it.

`fb plan TICKER --style position|swing|intraday` gives size, horizon and exits for any name and states the mode's
evidence first. `fb costs-calc` shows what a trade costs and what tax takes. Neither is advice.

**The edge is not the agent architecture** (replicable) **or the model** (rented). It is the point-in-time
dataset, the Indian promoter/group/pledging graph, the decision-outcome memory - and now the live record of
decisions made before their outcomes, which nothing else can supply.

## The daily loop

| Time (IST, weekdays) | What runs | Where it writes |
|---|---|---|
| 08:52 | Reminder if today's Kite token is missing: run `scripts\kite_login.cmd` (the owner logs in; nothing automates credentials) | notification |
| 09:05 | Windows task *FinanceBrain Paper Intraday* starts paper trading and the stock/options watch loop (both exit after the close) | `data/paper_live.duckdb`, `data/forecasts/watch.jsonl`; option-book archive is default-off pending data rights |
| 09:30 | **The board**: every liquid name ranked by expected net alpha, the five best with invalidation level and plan size, and the morning setups; "nothing clears costs" when that is true | `data/paper_reports/DATE-board-live.md` |
| 09:30-15:15 | Paper trading of the intraday rules (version live-v1, pre-registered), every trade written as it opens and closes | ledger |
| 15:15 | Flat; same-day outcome for every board name, so the top five are scored against the rest | `DATE-live.md` |
| ~15:50 | Review task: code errors only; rules never change with the day's P&L | notification |
| 18:45 | Windows task *FinanceBrain Daily Refresh*: prices, indices, actions, announcements, features; champion picks recorded and matured cohorts resolved; F&O snapshot; losing situations proposed as hypotheses; both suggestion books sell and buy on paper; your Kite holdings synced (read only); the terminal rebuilt; desktop alerts | research DB, `data/terminal/index.html` |

`fb terminal` rebuilds the one-page view at any time; open `data/terminal/index.html` in a browser.
`fb paper-intraday --report` prints the accumulated evidence per strategy (COLLECTING until 100 trades, then
LOSING / WINNING / NO_EDGE_DETECTED / DIFFERS_FROM_BACKTEST). `fb paper-intraday --replay DATE` replays a past day
with the same rules, labelled `replay` and kept apart from live evidence. The watch loop has no order path. The
separate OMS retains its evidence, stage and human-approval locks.

The watch loop polls every 60 seconds during market hours. It reads the prior close's opportunity snapshot and
today's Kite quotes. A positive historical scanner result produces **WATCH BUY**, not a confirmed trade call. A held
stock crossing its recorded stop produces **SELL REVIEW**. For listed options it shows the nearest at-the-money call
and put, live two-sided quotes, expiry break-even and a bootstrap payoff scenario before fees. The F&O daily-close
archive reaches back to 2015, but the executable bid/ask archive is too short to validate an options strategy;
no options message says BUY or SELL. Forecasts are recorded before
their outcomes for later evaluation. To inspect without sending, run
`.venv\Scripts\python.exe -m financial_brain.signals.watch --once --dry-run` during market hours.

The watcher loads `.env` itself. If today's Kite session is missing or rejected, it sends one deduplicated login
reminder, pauses market signals, and retries after the owner completes `scripts\kite_login.cmd`; the paper loop also
waits for a usable session rather than exhausting its ordinary feed-error budget. A session still unavailable at
the close is recorded as `session_unavailable`, never as a completed paper day.

Desktop notifications need an interactive Windows session. To opt into phone messages, set
`FB_ALERT_TELEGRAM_BOT_TOKEN` and `FB_ALERT_TELEGRAM_CHAT_ID` in the owner's environment and start the bot's private
chat first. These secrets stay out of git. The Telegram transport follows the
[official Bot API](https://core.telegram.org/bots/api#sendmessage). With no Kite session or fresh quote, no market
signal is sent. See [the edge-development results](docs/EDGE-DEVELOPMENT.md) and
[the success plan](docs/SUCCESS-PLAN.md) for the evidence and operating work that remains.

## Decisions still open

1. Personal tool or commercial product? — changes AGPL exposure, data budget, and whether
   SEBI RA/RIA registration is on the critical path
2. Does the point-in-time snapshot store start this month? — it is the only dataset that
   compounds and cannot be bought back later

Full list in [docs/RESOURCES.md §11](docs/RESOURCES.md).
