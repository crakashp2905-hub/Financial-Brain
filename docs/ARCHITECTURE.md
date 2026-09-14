# Financial-Brain — Architecture

The design decisions, contracts and rules. External tools and data are catalogued in
[RESOURCES.md](RESOURCES.md); Indian constraints in
[FEASIBILITY-INDIA.md](FEASIBILITY-INDIA.md).

---

## 1. What this is

An AI-native financial research system for Indian markets, in which the AI is the
analyst rather than the interface.

> **An AI-native financial research organization that understands the world's financial
> knowledge, continuously researches Indian markets, discovers and validates
> quantitative strategies, maintains a living financial knowledge graph, manages
> portfolios, and eventually executes validated decisions.**

It is not a chatbot, not a RAG system, and not a swarm of agents. It becomes a Financial
Brain only by operating a reliable, measurable, governed closed loop:

```
Observe → Verify → Model the market → Form a thesis → Size risk
   → Decide → Record → Observe outcome → Diagnose → Improve
```

The durable IP is the loop's memory:

```
Evidence → Thesis → Decision → Outcome → Postmortem → Calibrated future judgement
```

### The founding principle
> **LLM = reasoning/orchestration layer, not the source of truth.**
> Market data + fundamentals + news + technical indicators + the investor's own
> philosophy = source of truth.

Division of labour, never violated:
- the **LLM** reasons and orchestrates
- **quant engines** calculate
- **databases** remember
- **data providers** observe
- the **risk engine** constrains
- **Kite** executes — Kite is the hands, not the brain

The pipeline is `Data → Evidence → Context → Thesis → Risk → Decision → Outcome →
Learning`, never `LLM → predict price`.

---

## 2. Product charter — decide before architecture

Four candidate users imply four different products: long-term Indian-equity investor;
active swing trader; registered advisor/research team; institutional investor. They
differ in decision horizon, data latency, portfolio constraints, evaluation metrics, UX,
compliance and acceptable automation.

**First user:** a self-directed, long-term Indian-equity investor.
**First job:** *understand what changed in my portfolio/watchlist, why it matters, and
whether any recorded thesis has strengthened or weakened.*

**First product name:** *Indian Equity Research & Portfolio Copilot* — not a stock-tip
engine. Buy/sell/hold calls, targets, stop losses, model portfolios or personalised
recommendations delivered to **anyone else** trigger SEBI RA/RIA obligations and require
specialist legal advice, disclosures, records and an operating model designed for it.

---

## 3. The four brains

| Brain | Question it answers |
|---|---|
| 🧠 **Market Brain** | What is happening? Why? |
| 🧠 **Research Brain** | What does the world's financial knowledge tell us? |
| 🧠 **Quant Brain** | Can we turn that into a profitable, robust strategy? |
| 🧠 **Portfolio Brain** | Should we deploy it, how much, and what is the risk? |

---

## 4. Layered architecture

```
                    TERMINAL / COPILOT UI
                              │
                       AI ORCHESTRATOR
                              │
                       AGENT HARNESS
            (context · tools · permissions · evals)
                              │
     ┌────────────────────────┼────────────────────────┐
     ▼                        ▼                        ▼
MARKET BRAIN            QUANT BRAIN            RESEARCH BRAIN
     │                        │                        │
     └────────────────────────┼────────────────────────┘
                              ▼
                   PORTFOLIO / RISK BRAIN
                              ▼
                      EXECUTION BRAIN
                              ▼
                    HUMAN APPROVAL → KITE

Underneath, three distinct stores:
  KNOWLEDGE (graph) · CONTEXT (world state) · EXPERIENCE (decision DB)

Beneath those:
  DATA PLANE — provider abstraction over NSE / BSE / Kite / filings / RBI / news
```

### The three-store principle — implemented, not merely named

| Store | Purpose | Examples |
|---|---|---|
| **Knowledge** | Slow-changing sourced facts and relationships | ownership, products, sector, supplier links, promoter groups, policy concepts |
| **Context** | Current, time-bounded market world state | today's price, regime, results, events, portfolio exposure |
| **Experience** | Decision/outcome history | prior thesis, observed return, attribution, postmortem, calibration |

- **Knowledge**: Tata Motors owns X, operates in Y, supplier Z.
- **Context**: Tata Motors is down 4%, auto sector weak, crude rising.
- **Experience**: we bought Tata Motors under similar conditions six times; four worked,
  two failed.

> **The third store is what makes this a brain rather than a RAG system.**

No vector database or knowledge graph replaces the decision database. The decision
database *is* the empirical memory; the graph represents relationships; retrieval only
helps agents find evidence.

---

## 5. Data contracts

### 5.1 Canonical security master
One company/security resolves across NSE, BSE, Kite, Screener, Moneycontrol and filings.
**ISIN is the primary key** — symbols change, BSE codes differ, companies restructure.

Must carry: ISIN, exchange identifiers, symbols, company and group identity; corporate
actions (mergers, splits, bonuses, dividends, symbol changes, suspensions, delistings);
adjusted and unadjusted OHLCV with an explicit adjustment policy; financial-statement
period, filing date, publication time, revisions/restatements and source document;
**instrument universe membership at each historic point**; and entitlement, licence,
freshness and quality metadata per feed.

### 5.2 Four timestamps on every fact
- **event time** — when it occurred
- **publication time** — when it became public
- **ingestion time** — when Financial-Brain saw it
- **effective/as-of time** — the latest information that could validly have informed a
  decision

Indispensable against look-ahead and survivorship bias. In India this is not optional:
no vendor supplies point-in-time fundamentals, so we generate our own from day one by
never overwriting an observation.

### 5.3 Source hierarchy — Tier 1 wins on conflict
1. NSE, BSE, company filings, annual reports, investor presentations, regulatory filings
2. Kite market data, official company announcements
3. Screener, Moneycontrol, Economic Times, Business Standard, Reuters
4. News, social media, forums

### 5.4 Evidence ledger
Citation is not enough. Immutable ledger for every number, statement, extracted claim
and derived conclusion:

```
evidence_id → source URI/document → source tier → content hash → retrieval time
           → event/publication time → extraction/transformation → confidence
           → data-quality status → world-state version → decisions that used it
```

Claims never silently overwrite their source. A revised result, corrected filing or
reclassified entity creates a **new version** and retains the old. This is what makes a
recommendation reproducible and auditable.

---

## 6. Market World State

Agents reason from a **versioned world state**, not by independently browsing and
improvising. A snapshot contains:

- price, liquidity, volatility, breadth, index and sector state
- company fundamentals, filings, events, disclosure status
- macro/regime variables with their data-as-of time
- known relationships in the company/sector/policy graph
- portfolio positions, cash, exposures, constraints, open orders
- unresolved hypotheses, thesis invalidations, anomalies, missing data
- the complete evidence set and its data-quality assessment

The Orchestrator may commission research that produces *candidate* evidence. Only
verified, typed evidence updates the world state. **Every decision references exactly one
immutable world-state version.**

---

## 7. Market Brain

### Regime engine
Never evaluate a stock in isolation. Inputs: NIFTY, BANK NIFTY, NIFTY MIDCAP, NIFTY
SMALLCAP, India VIX, FII flows, DII flows, RBI, INR, crude, gold, US markets, US
Treasury yields, inflation, GDP, liquidity, sector rotation, market breadth.

Regime → strategy class: **BULL** → momentum · **SIDEWAYS** → mean reversion ·
**BEAR** → risk reduction.

A **Market Regime Agent sits above the individual stock analysts** — the piece missing
from the TradingAgents graph.

### Event Intelligence
- **Company**: results, earnings calls, investor presentations, management commentary,
  order wins/cancellations, acquisitions, divestments, promoter buying/selling, insider
  transactions, pledging, buybacks, dividends, splits, bonuses, credit-rating changes
- **Macro**: RBI decisions, inflation, GDP, PMI, rates, crude, USD/INR, policy, taxation,
  import/export restrictions
- **Market**: FII/DII flows, index rebalancing, sector rotation, volatility, breadth,
  derivatives positioning

### Event → sector → company → portfolio propagation
```
RBI raises repo
   → Banks +/- · NBFC − · Real Estate − · IT neutral
      → HDFC Bank moderate · Bajaj Finance high · DLF negative
         → your exposure: Bajaj Finance 8.2% → Monitor
```
The system must always answer: **why does this matter to *my* portfolio?**

### Indian Market Knowledge Graph
```
GOVERNMENT POLICY → DEFENCE SPENDING → {BEL, HAL, BDL} → supplier → component → commodity
RBI → Interest Rates → {Banks, NBFCs, Real Estate}
RBI → Liquidity → Market Regime
```
India-specific payoff the US market does not offer: **promoter group structures,
pledging, related-party transactions and group contagion**. Underbuilt, and graph-shaped.

### "What changed since yesterday?"
Daily diff across market, sectors and portfolio — each item with reason, new development,
technicals, risk and a recommendation. The first genuinely useful output, available long
before any strategy is validated.

---

## 8. Research Brain

### Ingestion
Papers, books, working papers, research reports, notes, existing strategies and academic
datasets → **Document Intelligence** → concepts, equations, assumptions, indicators,
factors, signals, entry rules, exit rules, portfolio rules, risk rules, empirical results
→ knowledge graph + research repository.

**Do not fine-tune an LLM on every book.** Store the source, chunk and index it, extract
concepts and formulas and rules, link concepts, preserve citations, represent claims and
evidence, make it retrievable. The LLM reasons *over* structured research memory.

### Investment Constitution
Built from the investor's own material: fundamental principles, technical principles,
valuation rules, risk rules, entry rules, exit rules, position sizing, portfolio
construction, categorical exclusions, chart patterns, examples of good and bad trades.
Agents retrieve from it.

### Hypothesis
A paper's claim becomes machine-testable:
```yaml
hypothesis:
  name: ...
universe: NSE equities
signal:   20_day_return > threshold
filter:   liquidity > minimum
regime:   trend_positive
entry:    momentum_rank > 90
exit:     momentum_rank < 50
risk:     volatility_adjusted_position_size
```

### Research knowledge graph
```
Paper → proposes Factor · uses Indicator · tested_on Market · supports Hypothesis
      · reports Performance → Strategy → {Signal, Risk, Regime} → {Entry, Sizing, Condition}
```
More sophisticated than RAG, and it preserves lineage:
`Paper → Hypothesis → Factor → Strategy → Backtest → Result → Regime → Live performance`.

---

## 9. Quant Brain

### Strategy Genome
Strategies are not isolated `strategy_001.py` files. They are combinations of primitives:
**Universe · Signal · Confirmation · Regime filter · Entry · Position sizing · Exit ·
Risk management** — which lets the system recombine and discover rather than hand-code.

### India Implementability Gate *(added for Indian markets)*
The Research Brain will happily read a US long-short momentum paper and generate a
hypothesis that cannot be traded here. Gate every hypothesis **before** backtesting:

```
Hypothesis → [ INDIA IMPLEMENTABILITY GATE ]
               long-only feasible?
               universe liquid enough at target AUM?
               turnover survives STT + impact?
               F&O available if shorting required?
               circuit / ASM / GSM / T2T exposure?
             → PASS → Factor Builder
             → FAIL → record the reason, do not backtest
```

### Alpha Validation Firewall — mandatory before paper trading
```
Research Agent → Hypothesis → Factor Generator → Backtest
                          ↓
        ┌──────────────────────────────────┐
        │        ALPHA VALIDATION          │
        │  IC / ICIR                       │
        │  Multiple-testing control        │
        │  Factor redundancy               │
        │  Deflated Sharpe                 │
        │  Walk-forward                    │
        │  Out-of-sample                   │
        │  Transaction costs               │
        │  Capacity                        │
        │  Regime stability                │
        └────────────────┬─────────────────┘
                         ↓
                  Strategy Judge → REJECT / PROMOTE
```
More necessary in India than the US: ~2,000 listed names, ~500 genuinely liquid, and
short clean history make false positives far likelier.

> **The AI is not allowed to declare itself successful. The validation layer does that.**

### Strategy evolution
Don't just pick the best Sharpe — ask *why* a strategy works, add the condition that
explains it, and re-test. Then candidate → validation → paper trading → promotion.

### Portfolio-aware selection
Strategy A at Sharpe 1.6 with 0.85 correlation to the existing portfolio is worth less
than Strategy B at Sharpe 1.4 with 0.15 correlation. Always evaluate at portfolio level:
correlation, factor exposure, drawdown, tail risk.

### Cost model — non-negotiable for India
STT on both legs of delivery, stamp duty, exchange transaction charges, SEBI turnover
fee, GST, brokerage; impact cost by liquidity bucket; circuit limits; ASM/GSM and T2T
flags. A backtest without these is fiction.

---

## 10. Two decision lanes

"No BUY without a backtest" applies cleanly to repeatable quantitative strategies, not to
every single-company fundamental thesis. Keep both lanes explicit:

| | **Quantitative lane** | **Fundamental / discretionary lane** |
|---|---|---|
| Gate | versioned code; PIT universe and data; realistic costs/slippage/liquidity; train-test separation; walk-forward, OOS, robustness, capacity | source quality; falsifiable thesis; comparable historical cases; scenario/risk analysis; portfolio constraints; continuous invalidation monitoring |
| Promotion | paper trading before promotion | outcome postmortem |

Both lanes share the same evidence ledger, world state, decision record, risk gate and
outcome store. **Neither may change live trading rules directly.**

---

## 11. Decision contract

Every recommendation is a structured, immutable record — never prose:

```
decision_id, instrument, universe, horizon, action, thesis,
supporting_evidence, contrary_evidence, primary_uncertainty,
world_state_version, expected payoff/scenarios, invalidation conditions,
entry/exit logic, sizing/risk budget, portfolio impact, approver,
execution status, outcome window, postmortem status
```

### Lifecycle
```
DRAFT → EVIDENCE VERIFIED → RISK REVIEWED → PAPER CANDIDATE
     → HUMAN-APPROVED → PROPOSED TO BROKER → EXECUTED
     → OUTCOME MEASURED → POSTMORTEM COMPLETE
```

Only the human-approval/execution policy advances a live-trade proposal. **No LLM, web
page, document, social post or agent workflow may bypass this state machine.**

### Presentation
Verdict · confidence % · supporting points ✓ · opposing points ✗ · market regime ·
preferred entry zone · invalidation level · exit conditions · data-as-of timestamp ·
sources. Every claim traceable.

Never `BUY — 87%`. Instead: BUY, confidence 72%, with evidence strength, agent agreement,
data quality, market regime and social signal rated separately, and the **primary
uncertainty named**.

### Counterfactual monitoring
Every thesis carries explicit invalidation conditions (e.g. revenue growth <5%, margin
<24%, large client losses, guidance downgrade, sector recession, USD/INR move, valuation
>35× PE) and the system **watches them continuously**.

---

## 12. Scores are calibrated decision aids, not predictions

A score is meaningful only if it declares: the instrument universe and horizon; the
benchmark and definition of success; point-in-time features and missing-data policy;
transaction-cost, liquidity and capacity assumptions; historical calibration by score
band, regime and market-cap segment; and uncertainty, data quality and disagreement
reported *separately* from the score.

Never present `82/100` as a probability of a price move.

### Entry and exit engines
Entry composite from fundamentals, valuation, trend, momentum, volume, sector, regime,
news sentiment and risk. Bands (80–100 strong setup · 70–79 watch/partial · 60–69 wait ·
40–59 avoid · <40 strong avoid) must be **learned and backtested, never chosen by hand**.

Exit is more important than entry and has distinct triggers: thesis failure · technical
failure · valuation · risk regime · portfolio concentration.

### Position sizing
Volatility, ATR, stop distance, portfolio correlation, sector concentration, max
drawdown, fractional Kelly, max position size → staged entry (initial / add / final),
never a bare "buy".

---

## 13. Agents

Start with **six plus an orchestrator**, not twenty: Data · Fundamental · Technical ·
News/Event · Portfolio/Risk · Decision.

### Committee flow
```
Fundamental · Technical · Sentiment analysts
        → Macro/Regime Analyst → Event Analyst
        → BULL vs BEAR debate → Thesis Engine
        → Trader → Risk Manager → Portfolio Manager
        → Paper trade → Outcome → Adapt
```

### Measure disagreement, don't eliminate it
Per-agent scores plus pairwise agreement (Fundamental↔Technical HIGH, Social↔Fundamental
LOW, Bull↔Bear HIGH). **Disagreement is itself information.**

### Per-agent track records drive dynamic weighting
Fundamental agent strongest in large-cap value, weakest in small-cap momentum; technical
agent best in short-term momentum, worst in sideways markets. Orchestrator reweights:
strong trend → technical ↑; earnings season → fundamental ↑; macro shock → macro + risk ↑.

### Skills, not monoliths
> **Don't build one giant Quant Agent. Build a library of procedural financial skills.**

### Tool surface
```
MARKET      get_quote · get_ohlcv · get_market_depth · get_index_data ·
            get_sector_data · get_portfolio
FUNDAMENTAL screener_query · get_financials · get_ratios · get_shareholding ·
            get_company_filings
NEWS        search_news · get_company_news · detect_events
SOCIAL      search_x · search_reddit · get_youtube_transcript ·
            sentiment_analysis · narrative_detection
ANALYSIS    technical_analysis · valuation_analysis · correlation ·
            regime_detection · anomaly_detection · risk_analysis
QUANT       backtest · walk_forward_test · monte_carlo · position_sizing · paper_trade
EXECUTION   create_order_proposal · risk_check · kite_order   ← locked initially
```

### Five layers
Perception (what's happening) → Reasoning (why) → Planning (what next) → Action (execute
the approved action) → Learning (was it correct).

```
PERCEIVE → REASON → PLAN → ACT → OBSERVE → EVALUATE → LEARN → PERCEIVE
```

---

## 14. Social intelligence — a separate, subordinate layer

Kept out of the fundamental/technical score. Pipeline: YouTube / news / Reddit / X →
sentiment engine → narrative engine.

Per-stock readout: sentiment and direction, post-volume change, top three narratives,
credibility, evidence split, change vs 7-day average.

Sub-scores: sentiment · sentiment momentum · discussion volume · narrative consistency ·
credibility · bot/manipulation risk · fundamental confirmation → composite Social Signal.
**Social Signal ≠ Buy Signal.**

Starting weights (to be learned, not hardcoded): Fundamental 35% · Technical 25% ·
Valuation 15% · Regime 10% · Events 10% · **Social 5%**.

### Social wakes the research team; it never trades
```
SOCIAL → narrative detection → anomaly detection → hypothesis → verify against reality
       → VERIFIED (raise weight) / UNVERIFIED (ignore or flag)
```
Worked example: small-cap mentions +650%, bullish posts +420%, abnormal volume. The agent
does **not** buy — it checks social → news → NSE announcements → BSE filings → company
site → management commentary → financials. No fundamental event ⇒
*"⚠ Unexplained social-media activity. Low-confidence signal. Possible promotional
activity. Do not act."*

Also detect **narrative-vs-reality divergence**: extremely bullish social against
unchanged revenue, deteriorating cash flow and 60× earnings ⇒ narrative reliability LOW.
The inverse can flag contrarian opportunities.

Treat social as **evidence with uncertainty** — noisy, manipulable, delayed, and subject
to platform/API restrictions.

---

## 15. Learning — controlled, attributed, reversible

### Never learn by self-modification
❌ trade loses → LLM rewrites its prompt → trade again
✅ trade loses → record decision → determine *why* → update performance statistics →
test an alternative hypothesis → backtest → paper trade → promote only if validated

### Outcome attribution
Raw return is not learning. Attribute each completed decision to market beta, sector
move, factor exposure, earnings surprise, valuation change, timing, execution cost,
thesis error, risk-rule error or luck. Track accuracy by horizon, regime, sector,
market-cap band, strategy and decision type.

Produces genuine self-knowledge — e.g. large-cap value 78% thesis accuracy, mid-cap
growth 71%, small-cap momentum 53%, turnarounds 48%. **The system learns where its edge
is and isn't.**

### The adaptation loop
```
Observed outcome → attribution → hypothesis → versioned experiment
 → validation → paper trade → risk committee → human-approved promotion
```
Must support rollback, preserve old versions, and never make a silent prompt, model,
score or live-rule change.

---

## 16. Execution — the harness enforces, not the model

```
Agent → Proposed Order
      → Risk Validator → Exposure Validator → Position Validator
      → Market-state Validator → Compliance/Policy Validator
      → Human approval / execution policy
      → Broker
```

### Phased autonomy
1. Kite **read-only** → portfolio + market data → agent → recommendation
2. Agent → signal → paper trade → evaluate
3. Agent → trade proposal → **human approves** → Kite executes
4. *(indefinitely deferred)* agent → risk engine → automated execution

Kite requires a static IP for order placement and caps order rate; SEBI's retail-algo
framework governs automated order flow. **Verify current requirements with the broker
before writing the order path.**

---

## 17. Evaluation harness — built before autonomy

Maintain labelled Indian data and tests for:

- entity resolution and corporate-action accuracy
- filing/event extraction, classification and timeliness
- citation correctness, claim-to-evidence support, contradiction detection
- data freshness, missing-data handling, source-tier precedence
- regime-classification stability
- thesis-invalidation alert recall and false-positive rate
- decision calibration and performance vs declared benchmarks
- quant backtest reproducibility, point-in-time integrity, costs, leakage checks
- prompt-injection resistance and tool-permission enforcement
- portfolio-risk and order-policy rejection tests

The governing measure is **not** "did the AI sound insightful?" It is: *did this system
improve evidence quality, decision discipline, calibrated judgement and risk-adjusted
outcomes relative to the stated benchmark?*

---

## 18. Security and governance

Every browser page, PDF, social post, transcript, external tool result and user upload is
**untrusted content**. Attack surface: Browser → Internet → Research → Code execution →
Financial data → Portfolio → Broker.

**Agent Security Brain**: prompt-injection detection and isolation · credential vaulting ·
secret redaction · tool allowlists · least-privilege access · transaction scopes ·
immutable audit logs · sandboxed code execution · data-exfiltration controls.

Production also requires: data entitlements and retention rules · source licensing ·
privacy/consent policy for portfolio data and uploaded documents · observability ·
incident response · backups and disaster recovery · legal review of recommendation and
execution features.

---

## 19. Minimum viable Financial Brain

Deliberately narrow first vertical slice:

1. Official NSE/BSE disclosures plus permitted EOD price data, and one canonical security
   master
2. A versioned watchlist/portfolio world state
3. A cited **"What changed since yesterday?"** briefing
4. A company thesis card: supporting evidence, contrary evidence, uncertainty,
   invalidation conditions, data quality
5. Immutable decisions, alerts, outcome records and postmortems
6. Portfolio risk/concentration analysis and **paper-only** proposed actions

Only after that slice proves reliable: real-time data, broad social ingestion, complex
graph inference, agent organisations, strategy discovery, and human-approved broker
execution.

### What is explicitly not needed initially
Twenty integrated repositories · autonomous execution or RL · a Bloomberg-scale terminal ·
a large multi-agent hierarchy · a knowledge graph of every possible relationship · any
promise to predict winning stocks.

---

## 20. Build order

**Phase 0 — Foundations.** Security master on ISIN with symbol history · NSE+BSE bhavcopy
ingestion, full history · corporate-action adjustment · **PIT snapshot store recording
`observed_at` on every fundamental** · Indian cost model · point-in-time universe
snapshots.

**Phase 1 — Perception.** Kite read-only · announcements ingestion and Event Intelligence ·
Market Regime Brain · the daily "what changed" brief.

**Phase 2 — Research and committee.** Document Intelligence over filings · Investment
Constitution · six agents plus orchestrator · thesis memory with watched invalidation
conditions · every claim sourced and timestamped.

**Phase 3 — Quant, gated.** Price/technical factors first · India Implementability Gate ·
Alpha Validation Firewall · Strategy Judge → registry → paper trading.

**Phase 4 — Execution.** Proposals → risk engine → human approval → Kite, only after
6+ months of paper results, and only after verifying SEBI/broker requirements.

**Phase 5 — Learning loop.** Outcome memory · attribution · per-agent track records ·
dynamic weighting · adaptation engine. This is the actual IP, and it only becomes real
once earlier phases have recorded enough decisions to learn from.

---

## 21. Non-negotiable rules

1. The LLM orchestrates and reasons; it is never the source of truth.
2. Tier 1 sources win when sources disagree.
3. No quantitative strategy goes live without backtest, out-of-sample and walk-forward
   validation — and no Indian hypothesis is backtested before passing the
   Implementability Gate.
4. Every claim carries a source and a timestamp; every decision a confidence and a named
   primary uncertainty.
5. Every thesis carries explicit invalidation conditions, watched continuously.
6. Social media wakes the research team; it never triggers a trade.
7. The agent never edits its own live trading rules; learning happens through recorded
   outcomes, not self-modification.
8. Kite starts read-only. Paper trade → risk gates → human approval → execution.
9. The harness — not the model — enforces order validation.
10. Reuse/wrap/replace/build is decided per component, with licensing checked first.
11. Knowledge ≠ Context ≠ Experience — three stores, three purposes.
12. Don't build a Frankenstein: integrate a small core, evaluate the rest.
13. Every fact carries event, publication, ingestion and as-of time.
14. Scores are calibrated decision aids, never predictions.
15. Only the human-approval state machine advances a live-trade proposal.
