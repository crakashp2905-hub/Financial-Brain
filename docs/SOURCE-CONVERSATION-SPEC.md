# Financial Brain — Full Capture of the Design Conversation

Source: ChatGPT share "Indian Market Agent Design"
https://chatgpt.com/share/6aa6ee5b-ebd0-83e8-8332-2263aacfc8ce

Complete, ordered capture of every decision, component, warning and diagram from that
conversation. This is the founding spec for this repository.

---

## 0. The original ask

Build an agent for **Indian markets** that:
- pulls data from **Screener** and **Moneycontrol**, plus user-provided references (PDFs/books/notes)
- builds a knowledge base of **which stocks to invest in, which not to**, **right time to enter**, **right time to exit**
- learns chart-reading from user-supplied material
- has **situational awareness**
- can use a **Kite API**

Answer: don't build an LLM stock-picker. Build
**Data → Evidence → Context → Thesis → Risk → Decision → Outcome → Learning.**

Core principle, never revised:

> **LLM = reasoning/orchestration layer, not the source of truth.**
> Market data + fundamentals + news + technical indicators + the user's investment
> philosophy = source of truth.

Division of labour:
- LLM **reasons and orchestrates**
- quant engines **calculate**
- databases **remember**
- data providers **observe**
- risk engine **constrains**
- Kite **executes** — Kite is the *hands*, not the brain

---

## 1. Components missing from the naive design

### 1.1 Market Regime Engine (situational awareness)
Never evaluate a stock in isolation. Inputs: NIFTY, BANK NIFTY, NIFTY MIDCAP, NIFTY
SMALLCAP, India VIX, FII flows, DII flows, RBI, INR, crude, gold, US markets, US
Treasury yields, inflation, GDP, liquidity, sector rotation, market breadth.

Regime → strategy: BULL → momentum · SIDEWAYS → mean reversion · BEAR → risk reduction.

### 1.2 Storage — do NOT use one database
- **PostgreSQL / TimescaleDB**: OHLCV, fundamentals, financial statements, ratios,
  portfolio, transactions, signals, scores, events
- **Vector DB**: annual reports, conference calls, news, user notes, books, research,
  investment theses, PDFs
- **Knowledge graph**: Company → Promoter → Subsidiary → Supplier → Customer → Sector
  → Government policy

Graph propagation: Policy → Component X → Supplier A → Company B → Margin impact →
Earnings estimate → Valuation.

### 1.3 Event Intelligence Engine
- **Company**: quarterly results, earnings calls, investor presentations, management
  commentary, order wins, order cancellations, acquisitions, divestments, promoter
  buying/selling, insider transactions, pledging, buybacks, dividends, splits, bonuses,
  credit-rating changes
- **Macro**: RBI decisions, inflation, GDP, PMI, interest rates, crude, USD/INR,
  government policy, taxation, import/export restrictions
- **Market**: FII/DII flows, index rebalancing, sector rotation, volatility, breadth,
  derivatives positioning

Prefer official exchange data (NSE corporate disclosures: fundamentals, announcements,
shareholding patterns) over third-party sites as source of truth.

### 1.4 News/Event → Stock Impact Engine
Event (RBI raises repo) → sector impacts (Banks +/-, NBFC -, Real Estate -, IT neutral,
Insurance …) → company impacts (HDFC Bank moderate, Bajaj Finance high, DLF negative)
→ **portfolio impact** (exposure: Bajaj Finance 8.2% → Monitor).
The system must answer: *why does this matter to MY portfolio?*

### 1.5 "What changed since yesterday?" engine
Daily diff of market, sectors, portfolio, each with reason, new development, technicals,
risk, and a recommendation (e.g. "50DMA broken / Risk elevated / DO NOT AVERAGE YET").

### 1.6 Entry Engine — structured score, not an LLM verdict
Fundamentals · Valuation · Trend · Momentum · Volume · Sector · Market regime ·
News sentiment · Risk → FINAL /100.
Bands: 80–100 strong setup · 70–79 watch/partial · 60–69 wait · 40–59 avoid · <40 strong avoid.
**Thresholds must be learned/backtested, not arbitrary.**

### 1.7 Exit Engine (argued to be more important than entry)
- **Thesis failure** (revenue deterioration + margin compression + guidance cut) → EXIT
- **Technical failure** (major support broken + high volume + trend reversal) → REDUCE/EXIT
- **Valuation** (price >> intrinsic value) → TAKE PROFIT / WAIT
- **Risk regime** (market → risk-off) → reduce exposure
- **Portfolio concentration** (24% vs 15% max) → rebalance

### 1.8 Position Sizing Engine
Inputs: volatility, ATR, stop distance, portfolio correlation, sector concentration,
max drawdown, Kelly / fractional Kelly, max position size.
Output: staged entry (initial / add / final), not a single "buy".

### 1.9 Portfolio awareness (via Kite)
Reason about portfolio-level risk, not per-stock. Kite Connect provides portfolio
management + live market data; paid plan includes live WebSocket data and historical candles.

### 1.10 Backtesting engine — mandatory before any BUY is trusted
Example strategy: ROCE>20%, D/E<0.5, Revenue CAGR>10%, RSI<45, Price>200DMA,
regime=bullish. Backtest 2016–2026 → CAGR, Max DD, Sharpe, win rate, trade count.
Compare vs NIFTY 50, NIFTY 500, buy & hold. **Walk-forward test**, or you build a
convincing overfit machine.

### 1.11 Memory — six kinds
- **Semantic**: what does ROCE mean?
- **Episodic**: what happened last time I analysed TCS?
- **Procedural**: how do I evaluate a breakout?
- **Portfolio**: what do I own?
- **Decision**: why did the agent recommend INFY?
- **Outcome**: what happened afterwards? ← most valuable

Loop: Prediction → Decision → Trade → Outcome → Error analysis → Strategy improvement

### 1.12 Explainable recommendation format
Verdict, confidence %, supporting points (✓), opposing points (✗), market regime,
preferred entry zone, invalidation level, exit conditions, data-as-of timestamp,
sources. Every claim traceable to data.

### 1.13 Source hierarchy (hallucination control)
- **Tier 1**: NSE, BSE, company filings, annual reports, investor presentations, regulatory filings
- **Tier 2**: Kite market data, official company announcements
- **Tier 3**: Screener, Moneycontrol, Economic Times, Business Standard, Reuters
- **Tier 4**: news / social media / forums

When sources disagree → **Tier 1 wins.**

### 1.14 Legal / data-rights cautions (explicit)
- Public accessibility ≠ right to scrape, store, redistribute or commercialize. Check
  Screener/Moneycontrol terms; prefer official or licensed feeds.
- NSE has explicit policies on use, display, distribution and redistribution of market
  data; commercial use needs the appropriate agreement. Real-time and corporate-data
  products are paid (a domestic corporate-data subscription listed at ₹10.6 lakh/year).
- NSE's AI-facing MCP offering is described as educational/informational, **not** for
  real-time trading or commercial deployment — distinct from licensed production feeds.
- **SEBI** has a specific framework for retail algorithmic trading; the execution layer
  must be designed to broker/exchange rules.
- Kite: static IP required for API order placement; documented limit 10 orders/sec per client.

### 1.15 Kite must start READ-ONLY — phased autonomy
1. Kite → portfolio + market data → agent → recommendation
2. Agent → signal → paper trade → evaluate
3. Agent → trade proposal → **human approves** → Kite executes
4. Only much later: agent → risk engine → automated execution

### 1.16 Counterfactual reasoning ("what would invalidate this?")
TCS buy thesis: revenue growth <5%, margin <24%, large client losses, guidance
downgrade, IT sector recession, USD/INR move, valuation >35× PE. The agent then
**continuously watches those conditions**.

### 1.17 Investment Thesis Graph
Thesis → {Fundamentals, Technical, Macro} → decision (BUY/WAIT/EXIT), every node
carrying **source + timestamp + confidence + historical outcome**.

### 1.18 First tech stack
Backend: Python, FastAPI, Pydantic, PostgreSQL, TimescaleDB, Redis, Qdrant/pgvector.
Data: Kite Connect, official NSE/BSE where available, Screener, Moneycontrol, filings,
annual reports, RSS/news.
AI: LLM for reasoning/orchestration, embedding model, RAG, structured tool calling,
later smaller specialist models.
Quant: pandas, NumPy, scipy, statsmodels, vectorbt/custom backtester, TA-Lib or pandas-ta.
Agents: start with **6**, not 20 — Data, Fundamental, Technical, News/Event,
Portfolio/Risk, Decision — plus an Orchestrator.

### 1.19 Staged roadmap (first version)
- **V1** Research Agent: Screener + Moneycontrol + NSE + user PDFs + Kite data → BUY/WAIT/AVOID + why
- **V2** Situational awareness: regime, sector rotation, news, corporate events, macro, portfolio awareness
- **V3** Quant engine: backtesting, signal generation, position sizing, risk management, walk-forward
- **V4** Personal Investment Twin: user's historical decisions, rules, mistakes, winning trades, chart interpretations
- **V5** Kite: proposal → risk checks → human approval → execute
- **V6** Autonomous monitoring of portfolio, watchlist, regime, fundamentals, news, invalidation conditions

Also noted: Indian situational awareness + multilingual financial documents + agentic
reasoning + personal investment memory is a strong fit for the **Sarvam startup program**.

---

## 2. Social & Sentiment Intelligence layer

Keep social **separate** from the fundamental/technical score.

Pipeline: Reddit / X / YouTube → Sentiment Engine (bullish/bearish/neutral) → Narrative Engine.

Per-stock readout: sentiment score + direction, post-volume change, first/second/third
narratives, credibility, evidence split (% negative/neutral/positive), change vs 7-day average.

Cross-reference chain: SOCIAL negative → NEWS confirms → FUNDAMENTALS margins declining
→ TECHNICAL support broken → ⚠ THESIS RISK INCREASED.

**Narrative-vs-reality divergence**: social extremely bullish but revenue unchanged,
cash flow deteriorating, 60× earnings, no order growth → narrative reliability LOW.
The inverse (very negative social, strong fundamentals, filings don't support the
negativity) flags **contrarian opportunities**.

Social sub-scores: Sentiment, Sentiment momentum, Discussion volume, Narrative
consistency, Credibility, Bot/manipulation risk, Fundamental confirmation → composite
Social Signal /100. **Social Signal ≠ Buy Signal.**

Initial decision weights (to be *learned by backtesting*, not hardcoded):
Fundamental 35% · Technical 25% · Valuation 15% · Market Regime 10% · Events 10% · Social 5%.

**Sentiment anomaly detection**: 2,000 posts/day → 18,000 (+800%), 82% bullish →
UNUSUAL SOCIAL ACTIVITY → is there a real news event? No → possible coordinated activity.

**Source credibility model**: per-source historical accuracy → HIGH/MEDIUM/LOW weight;
new accounts / promotional patterns get LOW. Builds a source reputation model over time.

**YouTube** ingestion: transcripts, titles, descriptions, comments, upload frequency,
engagement — claims always verified against primary sources.

Six-dimensional intelligence layer: Fundamental · Technical · Macro/Regime · Social ·
Events → Evidence Engine → Thesis Engine → Risk Engine → Decision Engine → BUY/WAIT/EXIT.

Caution: social data is noisy, manipulable, delayed and subject to platform/API
restrictions — **evidence with uncertainty**, never ground truth or a standalone trigger.

Social must **wake up the research team**, not place trades:
SOCIAL → narrative detection → anomaly detection → hypothesis → verify against reality
→ VERIFIED (increase weight) / UNVERIFIED (ignore or flag).

Worked example: 10:00 AM, small-cap mentions +650%, bullish posts +420%, abnormal
volume → the agent does **not** buy; it investigates social → news → NSE announcements
→ BSE filings → company website → management commentary → financials. No fundamental
event → "⚠️ Unexplained social-media activity. Low-confidence signal. Possible
promotional activity. Do not act."

(An Instagram Reel link was shared but could not be fetched — throttled, no indexed
copy; an upload/transcript was requested instead.)

---

## 3. The autonomous-agent shift (Adaptation Engine)

Shift from "AI that analyzes markets for you" to an agent with **objective, tools,
memory, feedback loop and ability to act**. The valuable part is *not* autonomous
trading — it is the **adaptive decision loop**.

AI Researcher capabilities: search market, screen stocks, read filings, analyse charts,
analyse news, analyse social media, analyse macro, examine portfolio → FORM HYPOTHESIS
→ TEST HYPOTHESIS → TAKE ACTION → OBSERVE RESULT → UPDATE BELIEFS → CHANGE STRATEGY.

### Adaptation Engine
Strategy A (strong fundamentals + price>200DMA + positive momentum) runs 6 months →
win rate 42%, poor Sharpe, high drawdown. Don't continue blindly; investigate: regime
changed? overfit? sector concentration? signal degradation? data problem? Then try
B/C/D → backtest → select robust alternative.

Constraint: **the agent must not rewrite its own trading rules and deploy them with
real money.** Candidates → backtest → paper trade → risk gates → human approval.

### Never learn by self-modification
Bad: trade loses → LLM changes its prompt → trade again.
Good: trade loses → record decision → determine WHY → update performance statistics →
test alternative hypothesis → backtest → paper trade → promote only if validated.
The agent develops **empirical memory**, not uncontrolled self-modification.

### Perception → World State
MARKET WORLD (price, fundamentals, news, social, macro; via Kite, Screener,
Moneycontrol, X/Reddit, RBI etc.) → PERCEPTION ENGINE → **SITUATIONAL MODEL** (the
agent's current understanding). Every decision is made against this world state.

### Give it goals, not commands

### Tool surface (locked items marked)
```
MARKET TOOLS: get_quote, get_ohlcv, get_market_depth, get_index_data,
              get_sector_data, get_portfolio
FUNDAMENTAL:  screener_query, get_financials, get_ratios, get_shareholding,
              get_company_filings
NEWS:         search_news, get_company_news, detect_events
SOCIAL:       search_x, search_reddit, get_youtube_transcript, sentiment_analysis,
              narrative_detection
ANALYSIS:     technical_analysis, valuation_analysis, correlation, regime_detection,
              anomaly_detection, risk_analysis
QUANT:        backtest, walk_forward_test, monte_carlo, position_sizing, paper_trade
EXECUTION:    create_order_proposal, risk_check, kite_order   ← locked initially
```

### Five-layer agent
1. **Perception** — what's happening?
2. **Reasoning** — why is it happening?
3. **Planning** — what should I investigate/do next?
4. **Action** — execute the approved action
5. **Learning** — was my decision correct?

Loop: PERCEIVE → REASON → PLAN → ACT → OBSERVE → EVALUATE → LEARN → PERCEIVE

### Named architecture: "Indian Market Autonomous Research Agent"
GOAL → ORCHESTRATOR → {PERCEPTION, RESEARCH, QUANT} → WORLD STATE (market regime,
sector regime, narratives, risks, opportunities) → THESIS ENGINE → BUY/WAIT/EXIT →
PAPER TRADING → OUTCOME ENGINE → ADAPTATION ENGINE → NEW HYPOTHESIS.

---

## 4. TradingAgents (multi-agent reasoning skeleton)

Open source under **Apache-2.0**; uses LangGraph; current repo adds structured outputs,
persistent decision logs, checkpointing, multiple model providers. Existing roles:
Fundamental / Sentiment / News / Technical analysts → Bull & Bear researchers → Trader
→ Risk Management → Portfolio Manager.

**Do not simply fork it and swap US data for Indian data.** Use it as the multi-agent
reasoning skeleton and build a far more capable Indian intelligence layer around it.

Three ideas converge: (1) Indian market knowledge + Screener + Moneycontrol + Kite +
the user's investment knowledge; (2) objective → investigate → act → observe → adapt;
(3) specialist agents debating before a portfolio decision. Result:
**an autonomous Indian-market investment research firm inside software.**

Extended graph: USER OBJECTIVE → ORCHESTRATOR → {Fundamental (Screener, NSE/BSE,
filings, results) · Technical (Kite OHLCV, indicators, patterns, volume) · Sentiment
(X/Reddit, YouTube, news, narratives)} → MACRO/REGIME ANALYST → EVENT ANALYST →
BULL vs BEAR DEBATE → THESIS ENGINE → TRADER AGENT → RISK MANAGEMENT → PORTFOLIO
MANAGER → PAPER TRADE/SIMULATE → OUTCOME/LEARNING → ADAPT.

**Missing agent in TradingAgents that is crucial here: the Market Regime Agent**,
sitting *above* the individual stock analysts.

### Your Investment Constitution
From the user's PDFs/books/notes: fundamental principles, technical principles,
valuation rules, risk rules, entry rules, exit rules, position sizing, portfolio
construction, things you refuse to invest in, chart patterns, examples of good trades,
examples of bad trades. Agents retrieve from it.

### Measure disagreement — don't eliminate it
Per-stock agent scores (INFY: Fundamental 82, Technical 76, News 71, Sentiment 84,
Macro 59; Bull thesis 81, Bear 63, Risk 72) plus pairwise agreement
(Fundamental↔Technical HIGH, Social↔Fundamental LOW, Bull↔Bear disagreement HIGH).
**Disagreement is itself information.**

### Confidence dimension — never "BUY — 87%"
Instead: BUY, Confidence 72%, with Evidence strength, Agent agreement, Data quality,
Market regime, Social signal each rated, plus **Primary uncertainty** named.

### Per-agent track records → dynamic weighting
Fundamental Agent 74/100 correct, strongest large-cap value, weakest small-cap
momentum; Technical Agent 61%, best short-term momentum, worst sideways markets.
Orchestrator reweights: strong trend → Technical ↑; earnings season → Fundamental ↑;
macro shock → Macro + Risk ↑. Genuine adaptive multi-agent intelligence.

**Do not start with autonomous Kite execution.** TradingAgents itself is
research-oriented and warns results vary with models, data and periods.

Build: TradingAgents (base orchestration) → Indian Market Intelligence Layer
{Indian Data Stack, Personal Investment Knowledge, Adaptive Learning Loop} → Kite
Connect → Paper → Approval → Execution.

First action recommended: dissect the TradingAgents repository — graph structure,
agents, state, tools, prompts, memory, data interfaces — and mark reuse vs replace.

---

## 5. Bloomberg-terminal ambition

Target: **a Bloomberg Terminal for Indian markets where the AI is the analyst, not just
the interface.** Bloomberg combines market data, news, research, analytics,
collaboration and execution, and has added agentic AI; it launched a Bloomberg India
digital hub in August 2026 (Indian market snapshots, securities data, FX, global
benchmarks, India-focused analysis).

Traditional: DATA → Terminal → Human analyst → Decision → Trade.
This product: DATA → AI MARKET MODEL → SPECIALIST AGENTS → DEBATE/VERIFICATION →
THESIS → RISK → DECISION → HUMAN/AUTOMATED EXECUTION → OUTCOME → LEARNING.

### Main screen (9:10 IST)
Header: NIFTY 50, BANK NIFTY, VIX, FII, DII. MARKET REGIME banner (🟡 NEUTRAL / HIGH
VOLATILITY) with Strong/Weak sectors. Two columns: **AI OPPORTUNITIES** (scored /100)
and **RISKS** (thesis deterioration, FII selling, social anomaly). **WHAT CHANGED?**
bullet feed. **ASK THE MARKET** prompt box.

### Killer feature: "Ask the market"
A question launches an *investigation*, not a search: Screen → Fundamental → Valuation
→ Sector → Technical → News → Social → Risk → Bull/Bear debate → shortlist, with a
reason per name.

### Company terminal
Price, market cap, P/E, ROCE, ROE, debt, plus AI SCORE /100 broken into Fundamental,
Valuation, Technical, Sentiment, Macro, Risk. Then the **AI Investment Committee**:
Bull case ✓ list, Bear case ✗ list, Risk Manager (max suggested allocation %),
Portfolio Manager decision (e.g. WATCH). Every statement opens its source.

### Coverage to build (India-first, not all of Bloomberg)
- **Equities**: NSE, BSE, large/mid/small caps, sector indices
- **Derivatives**: futures, options, Greeks, open interest, IV, PCR, option chains
  (NSE offers real-time data across capital markets, F&O, currency derivatives,
  including Level 1/2/3 and tick-by-tick feeds)
- **Corporate intelligence**: results, shareholding, promoter activity, corporate
  actions, announcements, filings
- **Macro**: RBI, inflation, GDP, PMI, INR, crude, bonds, yields
- **Alternative data**: X, Reddit, YouTube, Google Trends, news, web, earnings transcripts

### Terminal memory
THESIS #4821 stored with expected return, horizon, reasons, invalidation conditions.
Six months later: expected +18% vs actual +4%, which parts worked, and the learning
("agent overestimated multiple expansion"). Builds **institutional memory**, then a
self-knowledge table: large-cap value 78% thesis accuracy, mid-cap growth 71%,
small-cap momentum 53%, turnarounds 48%, PSU momentum 66% — **the system knows where
its edge is and isn't.**

### Portfolio OS
Portfolio value, Risk MEDIUM, Diversification 74/100, Concentration HIGH, Cash 12%,
sector exposure (IT 22%, Banks 18%, Energy 14%, Pharma 10%…), scenario simulation.
Then Kite as the execution layer: AI TERMINAL → {RESEARCH, PORTFOLIO} → RISK ENGINE →
TRADE PROPOSAL → USER APPROVAL → KITE. **No autonomous live trading initially.**

### The hard parts are not the AI
1. **Data rights** (NSE policies, paid products, ₹10.6 lakh/yr corporate data)
2. **Data normalization** — TCS vs TCS.NS vs 532540 → need one canonical security identity
3. **Historical data** — enough to test for real edge
4. **Latency** — real-time trading vs research have different requirements
5. **Evaluation** — measure *did the agent make better decisions*, not vibes

### Four stages
V1 Indian Equity Intelligence (no autonomous trading) · V2 AI Research Terminal
(agents + bull/bear + risk + thesis) · V3 Autonomous Research (goals → planning →
research → hypothesis → backtesting → paper → outcome → adaptation) · V4 Portfolio
Operating System (capital → terminal → research/portfolio/risk → Kite).

Verdict: reproducing all of Bloomberg is unrealistic as a first project; building the
best **AI-native Indian equity terminal** is achievable. Compete on *interpretation*,
not on having more data.

---

## 6. Fincept Terminal (called the most important repo shown)

https://github.com/Fincept-Corporation/FinceptTerminal

Architecture: native C++/Qt with embedded Python analytics; **54 screens, ~50 services,
40+ MCP tools, 16 equity/F&O brokers plus crypto exchanges**; bounded contexts for
Markets, News, Economics, Trading, Portfolio, Derivatives, Predictions, Agents, AI Chat;
workflow/node editor, paper trading, backtesting, broker integrations, quant modules.

**Do not build the terminal from zero** — treat Fincept as a foundation/reference.

| Project | Take |
|---|---|
| Fincept Terminal | Bloomberg-style terminal + data + analytics + portfolio + UI |
| TradingAgents | Multi-agent investment committee |
| Hummingbot | Algorithmic execution infrastructure |
| Vibe-Trading | Autonomous/self-improving trading workflows |
| NautilusTrader | Event-driven backtesting + trading infrastructure |
| skfolio | Portfolio optimization / risk |
| Kronos | Specialized financial time-series models |
| Own research | Indian-market intelligence + investment philosophy |

Plus **Kite** as the Indian broker/execution interface.

Fincept stack: DATA → ANALYTICS → AI → USER.
This product: DATA → PERCEPTION → SITUATIONAL AWARENESS → SPECIALIST AGENTS → DEBATE →
THESIS → RISK → DECISION → SIMULATION → OUTCOME → ADAPTATION. ← differentiator

Per-stock committee on clicking RELIANCE: Fundamental / Technical / Sentiment analysts →
Macro Analyst → Event Analyst → BULL vs BEAR → TRADER → RISK OFFICER → PORTFOLIO
MANAGER → DECISION, with Indian-market-aware analysts.

MCP tools to expose:
```
get_stock_price, get_ohlcv, get_option_chain,
get_screener_data, get_financial_statements, get_shareholding,
search_nse_filings, search_company_announcements,
search_news, search_x, search_reddit, search_youtube,
get_market_regime, get_sector_rotation, get_fii_dii_flow,
calculate_indicators, calculate_valuation,
backtest_strategy, run_monte_carlo,
get_portfolio, calculate_portfolio_risk,
create_trade_proposal
```

### What Fincept does NOT give: the Indian Market Knowledge Graph
Make it central. GOVERNMENT POLICY → DEFENCE SPENDING → {BEL, HAL, BDL} → supplier →
component → commodity. RBI → Interest Rates → {Banks, NBFCs, Real Estate};
RBI → Liquidity → Market Regime. Events propagate through the graph — that is
situational awareness.

### Steal Fincept's visual workflow builder
[NSE DATA] → [Market Regime] → [Screen Stocks] → {[Fundamental], [Technical]} →
[Bull/Bear] → [Risk Check] → [Paper Trade] — draggable nodes, so the system is useful
even without the LLM.

### Keep engines specialized, don't merge into one codebase
Fincept = terminal/UI + financial intelligence · TradingAgents = reasoning ·
NautilusTrader = event-driven quant trading/backtesting · Hummingbot = execution ·
skfolio = portfolio optimization · Kronos = time-series modelling ·
**own system = orchestration + Indian intelligence layer.**

### ⚠ License warning about Fincept
Public repo is **AGPL-3.0-or-later**. Modified distributions or offering the software as
a service can trigger AGPL source-release obligations; the Enterprise edition is
proprietary.
- Learning/personal: excellent
- Open-source project: fine if you comply with AGPL
- **Proprietary startup: do not casually fork and build proprietary features on top**
Decide **before** investing heavily in the codebase.

Layered architecture: TERMINAL UI (Fincept/Bloomberg-inspired) → AI ORCHESTRATOR →
{RESEARCH TEAM, MARKET INTEL, QUANT LAB} → KNOWLEDGE PLATFORM (SQL, time-series,
vector, graph, decision memory) → DECISION ENGINE → PAPER TRADE / HUMAN APPROVAL → KITE.

Identity: **an AI-native financial operating system for Indian markets** —
Bloomberg-like terminal + TradingAgents-style investment committee + autonomous
research + quantitative laboratory + portfolio manager + Indian knowledge graph +
broker execution, where **the system continuously learns from its decisions.**

Governing question for every component: **reuse / wrap / replace / build ourselves?**

---

## 7. Benchmarks: Finviz, InvosWealth, Messari

| Product | Does exceptionally well | What to take |
|---|---|---|
| Finviz | Visual market discovery + screening | Market visualization layer |
| InvosWealth | Indian quantitative stock research + signals | Indian quant/research layer |
| Messari | Deep asset intelligence + narratives + AI + event monitoring | Intelligence/knowledge layer |
| TradingAgents | Multi-agent investment reasoning | AI analyst layer |
| Fincept | Full financial-terminal architecture | Terminal/workstation layer |
| This system | Connects all of these | AI-native Indian financial OS |

### Finviz
Fundamental filters, technical filters, performance, ownership, charts, news, earnings,
portfolio, alerts, market maps, sector/industry visualization, saved screens.
**Finviz Matrix** organizes thousands of stocks by industry and market cap to show
where strength/weakness is developing.

Copy the **concept**, not the UI — an **India Market Matrix** (NIFTY / BANKNIFTY /
MIDCAP / SMALLCAP header + sector bars IT/BANKS/AUTO/FMCG/METALS/REALTY) — and go one
step further: **click a sector → AI explains why it's moving** (IT +2.1%: USD/INR
tailwind, US tech strength, TCS earnings revision, positive deal announcements, rising
institutional flows, social sentiment +18%; Regime impact Bullish; Confidence 82%).

### InvosWealth
QuantAI engine screening **4,000+ instruments** across fundamentals, value, momentum
and timing with a composite score, plus momentum radar, risk management, thematic
baskets. It is **research, not a trading platform** — a good benchmark for the
research/signal side.

Go further than `Stock → Quant Score`: Stock → Fundamentals → Valuation → Technical →
Momentum → News → Social → Macro → Market Regime → Portfolio Impact →
AI Research Committee → Decision.

### Messari — biggest conceptual inspiration for the intelligence architecture
Combines market data, asset/project information, research, news, fundraising, event
tracking, social intelligence, narratives, AI, monitoring, watchlists; APIs across
market, on-chain, news, fundraising, AI, social signals, event monitoring. Its
**Signals** system answers "what is the market talking about?" decomposed as
**Mindshare → Sentiment → Why**, tracking discussion volume, sentiment, topics and
influential voices.

### Steal Messari's WHAT / HOW / WHY for every Indian stock
- **WHAT**: Tata Motors +4.7%, Volume +182%, Social mentions +240%, News +37%
- **HOW**: Price 🟢 · Volume 🟢 · Sentiment 🟢 · Momentum 🟢 · Institutions 🟡 · Technicals 🟢
- **WHY**: EV demand expectations, margin commentary, sector-wide auto momentum; *but*
  valuation expanded 14% in a month while earnings estimates rose 4% → "Bullish, but
  increasingly valuation-sensitive." Far more useful than "BUY TATAMOTORS."

### Messari Copilot → "Market Copilot"
Natural-language questions over structured + unstructured financial information **with
citations**. A question triggers a MARKET INVESTIGATION (NIFTY IT +2.3%, USD/INR +0.4%,
Nasdaq +1.1%, US IT spending expectations ↑, TCS +3.1%, Infosys +2.7%) routed through
Research → News → Macro → Technical → Social agents → Bull/Bear debate → FINAL
EXPLANATION. **Every claim must have a source.**

### Combined positioning
Finviz = *see* the market · Invos = *quantify* it · Messari = *understand* it ·
TradingAgents = *debate* it · Fincept = *operate* the terminal ·
this system = **let AI continuously investigate, remember, reason, test and act on it.**

Plus the layer none of them provides properly: the **overnight situational-awareness
brief** — "12 important things changed overnight": FII selling accelerated, IT momentum
turned positive, crude crossed $X, INR weakened, 7 stocks entered breakout conditions,
3 portfolio holdings had thesis deterioration, small-cap breadth weakened, etc.

Suggested next step: a feature-by-feature teardown of Finviz vs Invos vs Messari vs
Fincept vs TradingAgents, marking each feature **REUSE / BUILD / BUY / DON'T BUILD**.

---

## 8. The Quant Research Brain (papers → hypotheses → strategies → live)

Aligned with current agentic-quant research (factor mining → signal discovery →
portfolio construction → execution → risk management, with memory and feedback loops).

### Stage 1 — Research ingestion
Papers, books, working papers, research reports, user notes, existing strategies,
academic datasets → **Document Intelligence** → extract concepts, equations,
assumptions, indicators, factors, signals, entry rules, exit rules, portfolio rules,
risk rules, empirical results → Knowledge Graph + Research Repository.

**Do not fine-tune an LLM on every book.** Instead: store the source, chunk/index,
extract concepts/formulas/strategy rules, link concepts, preserve citations, represent
claims and evidence, make them retrievable. The LLM reasons over this structured memory.

### Stage 2 — Strategy mining
A paper's claim ("momentum persists over X horizon under certain liquidity conditions")
becomes a machine-testable hypothesis:
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

### Stage 3 — Strategy laboratory
Research Brain → Hypothesis → Strategy Compiler → Executable Strategy → Backtest Engine
(CAGR, Sharpe, Sortino, Max Drawdown, Win Rate, Turnover, Costs, Slippage, Tail Risk)
→ Robustness Lab.

Rigour is non-negotiable: train/test separation, walk-forward, transaction costs,
slippage, liquidity constraints, survivorship-bias controls, look-ahead-bias controls,
parameter sensitivity, regime testing, Monte Carlo, out-of-sample, benchmark comparison.
(AgentQuant cited: research → strategy construction → backtesting → walk-forward /
Monte Carlo validation → trade diagnosis.)

### Strategy Evolution
Don't just pick the best Sharpe. Ask **why** a strategy works (B works in high liquidity
+ trending + low volatility), then generate `B + Regime Filter`, backtest again
(Sharpe 1.4→1.8, DD 18%→14%), then candidate → validation → paper trading → promotion.

### Strategy Genome
Stop treating strategies as isolated `strategy_001.py` files. Represent them as
combinations of primitives: **Universe · Signal · Confirmation · Regime filter · Entry ·
Position sizing · Exit · Risk management.** The agent recombines (Momentum + Volatility
filter + Trend regime + ATR sizing vs Value + Earnings revision + Momentum confirmation
+ Market regime) → **machine-assisted strategy discovery**.

### Knowledge-graph representation of research
Paper → proposes Factor · uses Indicator · tested_on Market · supports Hypothesis ·
reports Performance → Strategy → {Signal, Risk, Regime} → {Entry, Sizing, Condition}.
More sophisticated than RAG.

### Quant Brain ↔ Market Brain
Quant Brain *discovers* strategies; Market Brain decides *when to use them*. Momentum
strategy (Sharpe 1.7, trending markets) → Strategy Registry → Market Brain checks
regime=Trending, India VIX low, breadth strong, sector rotation positive → activated.
On regime change to risk-off: strategy confidence ↓ → position sizes ↓.
**Situationally aware quantitative trading.**

### Portfolio-aware strategy selection
Strategy A Sharpe 1.6 with 0.85 correlation to the existing portfolio vs Strategy B
Sharpe 1.4 with 0.15 correlation → **B may be far more valuable.**
Strategy Research → Strategy Evaluation → Portfolio Simulation (correlation, factor
exposure, drawdown, tail risk) → Portfolio Manager.

### Closed research loop
PAPERS/BOOKS/DATA/NOTES → RESEARCH BRAIN → HYPOTHESES → STRATEGY BUILDER → BACKTESTER →
ROBUSTNESS ENGINE → PAPER TRADING → LIVE OBSERVATION → OUTCOMES → STRATEGY EVALUATOR →
STRATEGY MEMORY → NEW HYPOTHESES ↺
In parallel: MARKET DATA → MARKET BRAIN → CURRENT REGIME → which strategies run →
PORTFOLIO/RISK BRAIN → EXECUTION.

### Hard rule
The AI may not directly modify a live strategy and trade it. Path: AI proposes →
automated tests → backtest → out-of-sample → walk-forward → paper trading → risk
committee → human approval → live. Then increasing autonomy. Cited reason: general LLM
capability does not automatically translate into good live trading or risk management.

### Renamed product
**AI Financial Intelligence & Quant Research OS**, with four brains:
1. 🧠 **Market Brain** — what is happening? why?
2. 🧠 **Research Brain** — what does the world's financial knowledge tell us?
3. 🧠 **Quant Brain** — can we turn that into a profitable, robust strategy?
4. 🧠 **Portfolio Brain** — should we deploy it, how much, what is the risk?

**OntoBricks** sits underneath as the semantic/knowledge layer (Databricks Labs
open-source; turns Databricks/Unity Catalog data into a living knowledge graph:
OWL/RDFS ontologies, R2RML mappings, materialized triples, reasoning, SHACL validation,
GraphQL, **MCP access for agents** — the MCP interface is especially useful for exposing
the graph to agents).

Quantitative-finance ontology sketch: Strategy → {Signal, Factor, Portfolio} →
{RSI, MACD, VWAP, Momentum, Risk, Weight, Exposure} → Entry Rule → Exit Rule →
Backtest → Performance.

---

## 9. Paperclip + OpenBB + OntoBricks layering

Four layers:
- **Paperclip** → agent operating system / control plane
- **TradingAgents** → financial reasoning team
- **OpenBB** → financial data plane
- **OntoBricks** → semantic / knowledge-graph plane
…with the product's own IP above and between them.

### Paperclip as the agent organization
A control plane for teams of AI agents: roles, reporting relationships, budgets, goals,
tasks, delegation, auditability; agents awakened by schedules, assignments, mentions or
manual invocation, operating in short execution cycles/heartbeats. Replaces building:
agent registry, task manager, agent hierarchy, budgets, delegation, heartbeats, status,
approvals, audit logs.

Org chart: PAPERCLIP → CIO Agent + Quant Director → {Fundamental, Technical, News,
Research, Strategy, Risk} → Portfolio Manager.

### TradingAgents as the Investment Committee
Fundamental / Technical / News analysts → Bull vs Bear debate → Trader → Risk Manager →
Portfolio Manager. Paperclip is the *company* around that committee.

### OpenBB as the data layer
OpenBB's Open Data Platform is a layer for proprietary, licensed and public data,
exposing it to Python, REST APIs, MCP and analyst/quant applications.
NSE / BSE / KITE → OPENBB ODP → {Python quant, REST apps, MCP agents}.
Caveat: OpenBB is the **data abstraction layer, not the entire Indian data solution** —
build/adapt providers for NSE, BSE, Kite, company filings, RBI, SEBI, economic data,
news, social, Screener, Moneycontrol, subject to each provider's licensing/terms.

### OntoBricks as semantic memory
Companies (promoters, suppliers, customers, sectors, events), Strategies (factors,
signals, rules, results, regimes), Research (papers, books, concepts, claims, evidence).

### Full stack
AI FINANCIAL TERMINAL (Market Map │ Screener │ Company │ Portfolio │ Quant Lab │
Research │ Debate │ Graph │ Strategies │ AI Copilot)
→ PAPERCLIP CONTROL PLANE (Goals • Agents • Delegation • Budgets • Schedules •
Approvals • Governance • Audit • Agent Memory)
→ {MARKET BRAIN (TradingAgents-style committee), QUANT BRAIN (Strategy Lab,
Backtesting), RESEARCH BRAIN (Research, Literature)}
→ PORTFOLIO / RISK BRAIN → EXECUTION BRAIN → KITE/BROKER.
Underneath: DATA PLANE = OpenBB ODP (Market Data NSE/BSE/Kite · Fundamentals
Filings/Ratios · Alternative Data/News); KNOWLEDGE PLANE = OntoBricks (Companies •
People • Sectors • Strategies • Factors • Papers • Books • Claims • Evidence • Events •
Narratives).

### 🧪 Quant Research Lab as a separate autonomous org inside Paperclip
QUANT DIRECTOR → {Literature Researcher, Factor Miner, Data Scientist} →
{Hypothesis Agent, Strategy Builder} → Backtest Agent → {Robustness, Walk-forward,
Stress} → Strategy Judge → Reject / Candidate → Paper Trading → Live Candidate →
Human Approval.

### Agents create work for other agents
Literature Researcher finds a paper → TASK #1827 (investigate momentum crash conditions
in Indian equities → Quant Researcher) → TASK #1831 (build Indian momentum strategy
with crash regime filter → Strategy Builder) → TASK #1842 (backtest 2010–2026 with
transaction costs, slippage, survivorship controls, walk-forward → Backtest Agent) →
TASK #1854 (evaluate robustness vs NIFTY 500, NIFTY Momentum, buy-and-hold →
Strategy Judge). **That is not a chatbot — it is a research organization.**

### The graph remembers the whole lineage
Paper → Hypothesis → Factor → Strategy → Backtest → Result → Regime → Live performance,
so months later the system can interrogate its own research history.

### Reuse vs Build (explicit)
**Reuse:** Paperclip (agent org/control plane) · OpenBB (data abstraction/providers) ·
TradingAgents (investment committee/reasoning patterns) · OntoBricks (knowledge
graph/ontology) · NautilusTrader (event-driven backtesting/trading) · skfolio
(portfolio optimization) · Fincept (study terminal/UI architecture — **AGPL-3.0**,
review licensing before incorporating into a proprietary product).

**Build ourselves (the differentiated product):** Indian Market Intelligence · Quant
Research Brain · Research → Hypothesis → Strategy compiler · Strategy Genome ·
Market Regime Brain · Personal Investment Constitution · Thesis/Decision Memory ·
Outcome & adaptation engine · Indian financial knowledge-graph ontology · Agentic
investment committee · Portfolio-aware strategy selection · Natural-language financial
terminal · situational awareness.

### Product statement
> **An AI-native financial research organization that understands the world's financial
> knowledge, continuously researches Indian markets, discovers and validates
> quantitative strategies, maintains a living financial knowledge graph, manages
> portfolios, and eventually executes validated decisions.**

Stack roles: Paperclip = Organization · OpenBB = Senses/data · OntoBricks =
Memory/knowledge · TradingAgents = Investment reasoning · Quant Lab = Scientific
experimentation · NautilusTrader = Simulation/execution engine · Kite = Indian broker
gateway · own orchestration + research/adaptation layer = **the actual brain.**

---

## 10. Vibe-Trading

MIT-licensed; ~33k+ stars, 2,200+ commits; ReAct agent core, 69 finance skills,
29 swarm presets, multiple backtesting engines, MCP tools, React frontend.

Provides: ReAct trading agent · 69 specialized finance skills · 29 multi-agent swarm
presets · DAG-based orchestration · natural-language backtesting · factor IC/IR
analysis · quantile backtesting · technical pattern analysis · options/Greeks ·
portfolio optimization · multiple asset classes · Monte Carlo · bootstrap confidence
intervals · walk-forward validation · strategy generation · report generation · MCP
interface · persistent run memory/artifacts · strategy export to Pine Script, TDX, MQL5.

Role split:
- **Paperclip**: who does what, current research objective, task ownership, what happens
  next, what failed, what needs approval, what budget exists, what's pending
- **Vibe-Trading**: how to research a strategy, construct it, backtest it, validate it,
  analyze factors, generate the report
- **TradingAgents**: how the financial analysts debate an investment

**Hypothesis Registry** (Hypothesis → Deterministic Backtest → Evidence Report) with
reproducible run cards, traces/citations and research artifacts — don't rebuild it,
extend it into research memory. Example: HYPOTHESIS #1842 — source Research Paper #284,
derived by Literature Agent, strategy Momentum_India_07, backtest 2010–2026, OOS PASS,
walk-forward PASS, Monte Carlo PASS, current regime ACTIVE, live evidence 7 months,
status VALIDATED → stored in OntoBricks.

**Research Knowledge Graph** (what Vibe-Trading doesn't fully provide):
Paper #184 → supports → Momentum Hypothesis → implemented as → Strategy #72 →
{tested on NSE, tested during Bull Regime, tested during Bear Regime, uses Momentum
Factor, uses Breadth Filter} → produced → Backtest #901 → {Sharpe, Drawdown, Turnover,
OOS Result}. The Market Brain queries it.

**India relevance**: newer development includes first-class Indian equity NSE/BSE
backtesting, a fundamental factor layer, and Indian-market data adapters in later
releases/forks. So:
- **KEEP**: strategy framework, backtesting, factor research, validation, skills, MCP
- **EXTEND**: NSE/BSE, Kite, Indian corporate data, Indian fundamentals, Indian
  transaction costs
- **REPLACE/ADD**: research memory, knowledge graph, literature ingestion, strategy
  genome, regime intelligence, long-term adaptation

**69 skills**: don't recreate. They seed a financial skill library — eventually a
**Financial Skill Graph**: Strategy Generation → {Momentum, Value, Mean Reversion} →
{Technical, Fundamental, Statistical} → {RSI, MACD, VWAP}, with agents dynamically
acquiring skills per research problem instead of hard-coded workflows.

**Revised Quant Research Brain**: PAPERCLIP → Research Director → {Literature
Researcher, Factor Scientist, Market Data Scientist} → Hypothesis Engine →
VIBE-TRADING → {Strategy Builder, Backtest Engine, Quant Analysis} → Validation Engine
→ {OOS, Walk-Forward, Monte Carlo} → Strategy Judge → FAIL / PASS → Strategy Registry →
Paper Trading → Live Candidate. **OntoBricks records the entire lineage.**

**Data flow**: NSE/BSE/Kite + company filings + news + macro → OPENBB (data
abstraction) → {Vibe-Trading, TradingAgents, Market Brain} → OntoBricks knowledge graph.

**Company metaphor**: CEO/Orchestrator → {Market Research Division (TradingAgents),
Quant Research Division (Vibe-Trading), Portfolio Division (Risk Engine)} → Knowledge
Brain (OntoBricks) → Data Platform (OpenBB) → NSE/BSE/Kite, with **Paperclip as the
organizational operating system.**

Do not fork Vibe-Trading and call it done — it should become one of the most important
*engines inside* the system.

Suggested next step: repository-level comparison of Vibe-Trading vs TradingAgents vs
Fincept vs Paperclip vs OpenBB — directories, agent architecture, MCP interfaces,
backtesting engines, memory systems, licenses — and exactly **which code to reuse vs
rewrite**, to design the actual monorepo.

---

## 11. Agent-infrastructure repos — ranked

| Project | Value | Where it fits |
|---|---:|---|
| OpenViking | ⭐⭐⭐⭐⭐ | Context + memory + skills |
| Awesome Harness Engineering | ⭐⭐⭐⭐⭐ | Agent reliability architecture |
| Browser Use | ⭐⭐⭐⭐⭐ | Web/data acquisition |
| Scientific Agent Skills | ⭐⭐⭐⭐ | Research methodology |
| Agent Memory | ⭐⭐⭐⭐ | Persistent agent memory |
| Diagram Design | ⭐⭐⭐ | Development/documentation |
| Anthropic Cybersecurity Skills | ⭐⭐⭐ | Security/agent hardening |
| Harness resources themselves | ⭐⭐⭐⭐⭐ | Design principles, not a code dependency |

Framing: this is no longer a trading app — it is an **agentic financial research
organization**, so memory, context, tool use, verification and autonomy matter most.

### 1. OpenViking
A context database where **memories, resources and skills live in one virtual
filesystem** (`viking://`), with hierarchical retrieval and **L0/L1/L2 context layers**,
dual-layer storage (AGFS content store — local, S3, memory — plus a vector index with a
single source of truth), CLI/SDK/HTTP interfaces, and automatic extraction of
6-category memories (profile, preferences, entities, events, cases, patterns).
Prerequisites noted: Python ≥3.10; Go ≥1.22 for some components.

Replace `AGENT → {Vector DB, SQL, Files}` with
`AGENT → CONTEXT OS (OpenViking) → {Memories → Decisions/Outcomes/Experiences,
Research → Papers/Books/Filings, Skills → Quant skills/Finance tools/Analysis methods}`.

Hierarchical retrieval: L0 "Momentum research" → L1 {Academic evidence, Indian-market
evidence, Factor construction, Regime effects, Transaction costs} → L2 {specific papers,
equations, datasets, backtests, implementation details} — instead of loading 10,000 docs.

⚠ **AGPL-3.0.** For a proprietary product, study and prototype against it; keep it
behind a **replaceable interface** until the licensing architecture is settled.

### 2. Agent Memory
Persistent memory for coding agents: confidence, lifecycle, knowledge graphs, hybrid
search, MCP interfaces (~95.2% retrieval recall on LongMemEval-S with ~92% fewer input
tokens than full context; zero external databases).

**Coding-agent memory ≠ financial memory.** This system needs: Company memory,
Strategy memory, Research memory, Decision memory, Portfolio memory, Agent memory,
Outcome memory, Market-regime memory, User-preference memory.

Canonical record to make permanent:
```
DECISION #8271
Stock: ABC
Thesis: Earnings acceleration + declining debt
Decision: BUY
Evidence: 12 sources
Agents: Fundamental 87 · Technical 64 · Sentiment 72 · Macro 61 · Risk 78
Entry: ₹X
Invalidation: Earnings growth < Y · ROCE deterioration · Debt reversal
Outcome: -14%
Postmortem: Thesis failed because margin compression was not detected early enough.
```
Useful — but **don't confuse it with the financial knowledge graph.**

### 3. Scientific Agent Skills (K-Dense)
~160+ scientific/research skills (scientific databases, statistical workflows,
time-series forecasting, scientific ML, 100+ databases). **Do not install all of them.**

Irrelevant: cancer genomics ❌ drug discovery ❌ molecular dynamics ❌ RNA velocity ❌
Keep/adapt: statistical analysis, time-series analysis, forecasting, Bayesian methods,
hypothesis testing, experimental design, data analysis, scientific literature search,
citation/provenance, ML experimentation, visualization, reproducible research.

Then build our own **Financial Agent Skills**:
```
quant/
├── factor-research
├── factor-ic-ir
├── momentum-analysis
├── value-analysis
├── mean-reversion
├── volatility
├── portfolio-construction
├── transaction-cost-analysis
├── walk-forward-validation
├── monte-carlo
├── regime-detection
├── event-study
├── backtesting
├── survivorship-bias-check
├── lookahead-bias-check
└── strategy-postmortem
```
Architectural principle:
> **Don't build one giant Quant Agent. Build a library of procedural financial skills.**

Caution: skills can execute code and modify agent behaviour — review, never blindly install.

### 4. Browser Use
Agents operating real browsers (click, type, navigate, extract, logged-in sites).
Hierarchy: **API → MCP/data connector → Browser Use** (last-mile fallback only).
Research use: Research Agent → search → company website → investor presentation →
download PDF → extract → knowledge graph.
Avoid browser automation for high-frequency market data. It introduces **prompt
injection, credential and data-exfiltration risks** → must sit behind a hardened harness.

### 5. Awesome Harness Engineering — the most important *idea*
Not a dependency; a design manual. Organizes agent reliability around context & memory,
tool design, constraints, safe autonomy, specs/workflows, orchestration, evaluation,
observability, benchmarks. (Ecosystem items noted: `templates/AGENTS.md`; Cowork Forge —
MIT multi-agent software-development workflow with specialized roles and a staged
pipeline from requirements through delivery.)

Not `LLM → Tools → Trade`, but:
AGENT HARNESS → {CONTEXT → KNOWLEDGE, TOOLS → DATA/API, MEMORY → EXPERIENCE} → AGENT →
{PLAN, EXECUTE, VERIFY} → EVALUATE → LEARN.

Applied to trading, the harness enforces: **the LLM cannot directly place arbitrary
orders.** Instead: Agent → Proposed Order → Risk Validator → Exposure Validator →
Position Validator → Market-state Validator → Compliance/Policy Validator →
Human approval / execution policy → Broker.

### 6. Anthropic Cybersecurity Skills — take the philosophy, not the library
754–817 structured cybersecurity skills mapped to MITRE ATT&CK, NIST CSF 2.0, MITRE
ATLAS, D3FEND, NIST AI RMF, MITRE F3; agentskills.io standard; Apache-2.0; 29 security
domains. **Community-created and explicitly not affiliated with Anthropic** despite the name.

The attack surface is real: Browser → Internet → Research → Code execution → Financial
data → Portfolio → Broker. So build a dedicated **Agent Security Brain**: prompt
injection detection · tool permission boundaries · credential isolation · browser
sandboxing · malicious document detection · untrusted-content isolation · MCP security ·
code execution sandbox · secrets management · audit logs · transaction approval ·
data exfiltration prevention.

### 7. Diagram Design — useful for development, not runtime
29–38 editorial diagram types for Claude Code, self-contained HTML + SVG, no Mermaid
slop; installable as a Claude Code plugin (`/plugin marketplace add
cathrynlavery/diagram-design`, `/plugin install diagram-design@diagram-design`); design
system in `skills/diagram-design/SKILL.md` plus `docs/cookbook.md`. (Related:
joehaddad2000/schematics — visual engineering skills for planning and explaining
technical work.)

Have the coding agent maintain:
```
docs/architecture/
├── system.html
├── agent-org.html
├── data-flow.html
├── quant-pipeline.html
├── execution-flow.html
├── knowledge-graph.html
└── research-loop.html
```

### Revised master architecture
FINANCIAL TERMINAL → AGENT ORGANIZATION (PAPERCLIP) → AGENT HARNESS (Context / Tools /
Permissions / Evals) → {MARKET INTELLIGENCE BRAIN (TradingAgents), QUANT RESEARCH BRAIN
(Vibe-Trading), PORTFOLIO BRAIN (Risk/Optimizer)} → CONTEXT / MEMORY (OpenViking /
memory system) → {ONTOBRICKS knowledge graph, RESEARCH CORPUS papers/books, SKILLS
finance skills} → OPENBB DATA PLANE → {NSE/BSE, KITE, Company Data} → Browser Use
(fallback research) → INTERNET.

### ⚠ Do NOT build a Frankenstein
Accumulated projects: Fincept, TradingAgents, Vibe-Trading, Paperclip, OpenBB,
OntoBricks, OpenViking, NautilusTrader, skfolio, Browser Use, Scientific Agent Skills,
Agent Memory, Harness Engineering, Diagram Design. Four buckets:

- 🟢 **Core, seriously integrate**: OpenBB (data abstraction) · Vibe-Trading (quant
  research) · TradingAgents (investment committee) · Paperclip (agent organization) ·
  NautilusTrader (backtesting/execution) · skfolio (portfolio optimization) ·
  OntoBricks (semantic graph)
- 🟡 **Infrastructure to evaluate**: OpenViking (context/memory) · Browser Use ·
  Agent Memory — decide whether OpenViking **replaces** part of the memory architecture
  rather than adding another memory system
- 🔵 **Methodology**: Awesome Harness Engineering — don't install it, **use it to design
  the system**
- 🟣 **Developer productivity**: Scientific Agent Skills · Diagram Design (selective)
- 🔴 **Security**: Cybersecurity Skills (selective, to build the agent-security layer)

### Three kinds of knowledge — the clearest architectural principle
FINANCIAL DIGITAL BRAIN → {KNOWLEDGE → OntoBricks, CONTEXT → OpenViking,
EXPERIENCE → Decision DB}

- **Knowledge**: Tata Motors owns X, operates in Y, supplier Z.
- **Context**: Tata Motors is down 4%, auto sector weak, crude rising.
- **Experience**: our system bought Tata Motors under similar conditions six times;
  four worked, two failed.

> **That third category is what makes this a digital brain rather than a RAG system.**

Loop: **Knowledge → Context → Decision → Outcome → Experience → Adaptation** —
identified as the **unique IP**.

Repeated caution: OpenViking is AGPL-3.0 → keep behind a replaceable interface until
the licensing architecture is settled.

---

## 12. Repository plan

Recommended repo (private). Proposed skeleton — **do not create all of it immediately**;
start with the architecture/specification, then add components one by one:

```text
ai-financial-brain/
├── apps/
│   └── terminal/
├── agents/
│   ├── market/
│   ├── quant/
│   ├── research/
│   ├── portfolio/
│   └── risk/
├── data/
│   ├── openbb/
│   ├── nse/
│   ├── bse/
│   └── kite/
├── knowledge/
│   ├── ontology/
│   ├── research/
│   └── memory/
├── strategies/
│   ├── hypotheses/
│   ├── backtests/
│   └── registry/
├── orchestration/
│   └── paperclip/
├── backtesting/
├── execution/
│   └── kite/
├── skills/
├── evaluation/
├── docs/
└── infrastructure/
```

### Status at the end of the conversation
The target repo `crakashp2905-hub/Financial-Brain` (private) could **not** be reached by
the ChatGPT GitHub connector — repeated `404`s. Nothing was ever written to it. The full
scope was explicitly listed as captured but unimplemented:

Paperclip, OpenBB, Vibe-Trading, TradingAgents, OntoBricks, NautilusTrader, skfolio,
Browser Use, OpenViking, agent memory, scientific skills, harness engineering, security,
the Indian NSE/BSE/Kite data plane, knowledge graph, quant research brain, literature
ingestion, hypothesis/strategy genome, backtesting and validation, market/regime brain,
portfolio/risk brain, decision/thesis memory, outcome learning, adaptation, terminal UI,
MCP/tool layer, paper/live execution safeguards, documentation, and licensing boundaries.

Remaining connector troubleshooting (GitHub Settings → Applications → the ChatGPT GitHub
app → Repository access → add `crakashp2905-hub/Financial-Brain`; or disconnect/reconnect
GitHub in ChatGPT Settings → Apps/Connectors and select only that repository) is **moot
here** — this repository is local and writable.

---

## 13. Licensing ledger (consolidated — decide before building)

| Project | License | Implication |
|---|---|---|
| TradingAgents | Apache-2.0 | Safe to use/extend |
| Vibe-Trading | MIT | Safe to use/extend |
| Anthropic Cybersecurity Skills | Apache-2.0 | Safe; not affiliated with Anthropic |
| Scientific Agent Skills | MIT | Safe; review skills before installing (they execute code) |
| Fincept Terminal | **AGPL-3.0-or-later** | Modified distribution or SaaS triggers source release; Enterprise edition is proprietary |
| OpenViking | **AGPL-3.0** | Keep behind a replaceable interface; decide before embedding/forking |
| OpenBB / OntoBricks / NautilusTrader / skfolio / Browser Use / Paperclip | verify per-repo | Confirm before integration |
| NSE market & corporate data | commercial agreement required | Paid; redistribution restricted; MCP offering is educational only |
| Screener / Moneycontrol | terms unclear | Public ≠ licensed; prefer official feeds |
| SEBI retail algo framework | regulatory | Constrains the execution layer |

---

## 14. Non-negotiable rules distilled

1. The LLM orchestrates and reasons; it is never the source of truth.
2. Tier 1 sources win when sources disagree.
3. No BUY without a backtest, out-of-sample and walk-forward validation.
4. Every claim carries a source and a timestamp; every decision a confidence and a named
   primary uncertainty.
5. Every thesis carries explicit invalidation conditions, watched continuously.
6. Social media wakes up the research team; it never triggers a trade.
7. The agent never edits its own live trading rules; learning happens through recorded
   outcomes, not self-modification.
8. Kite starts read-only. Paper trade → risk gates → human approval → execution.
9. The harness — not the model — enforces order validation (risk, exposure, position,
   market state, compliance).
10. Reuse/wrap/replace/build is decided per component, with licensing checked first.
11. Knowledge ≠ Context ≠ Experience — three stores, three purposes.
12. Don't build a Frankenstein: integrate a small core, evaluate the rest.

---

## 15. Repository sweep — 20 more repos filling architecture gaps

*(Added after the first capture; two later messages in the same conversation. The search
was explicitly scoped to repos that **fill gaps in Financial-Brain**, not more trading bots.)*

### Batch 1 — repos 1–10

| # | Repository | What it adds | Where it fits |
|---|---|---|---|
| 1 | [Microsoft Qlib](https://github.com/microsoft/qlib) | AI-oriented quant research, ML, factor mining, backtesting, portfolio + execution | 🧠 Quant Research Brain |
| 2 | [FinRL](https://github.com/AI4Finance-Foundation/FinRL) | Reinforcement-learning trading agents | 🤖 Strategy Evolution |
| 3 | [FinRL-Meta](https://github.com/AI4Finance-Foundation/FinRL-Meta) | Market environments + benchmarks for RL | 🧪 Research/Simulation |
| 4 | [MLFinLab](https://github.com/hudson-and-thames/mlfinlab) | Financial ML methodology, labeling, sampling, backtest statistics, robust research | 🔬 Quant Science |
| 5 | [PyPortfolioOpt](https://github.com/PyPortfolio/PyPortfolioOpt) | Mean-variance, Black-Litterman, HRP, portfolio optimization | 💼 Portfolio Brain |
| 6 | [Riskfolio-Lib](https://github.com/dcajasn/Riskfolio-Lib) | Large set of risk measures + constrained portfolio optimization | 🛡️ Risk Brain |
| 7 | [Zipline-Reloaded](https://github.com/stefan-jansen/zipline-reloaded) | Event-driven strategy backtesting | 🧪 Backtesting |
| 8 | [TA-Lib Python](https://github.com/TA-Lib/ta-lib-python) | 150+ technical indicators + candlestick patterns | 📈 Technical Analyst |
| 9 | [ML Quant Trading](https://github.com/initial-d/ml-quant-trading) | 213 factors, ML models, portfolio optimization, cost-aware backtesting | 🧬 Factor Research |
| 10 | [OptimalPortfolios](https://github.com/ArturSepp/OptimalPortfolios) | Institutional-style portfolio construction, covariance, alpha, constraints, rolling backtests | 💼 Portfolio/Risk Brain |

**Top three priorities:**

1. **Qlib** — "probably the most important discovery for your project." Microsoft
   describes it as an AI-oriented quantitative investment platform covering
   **alpha discovery → risk modeling → portfolio optimization → execution**, with ML,
   market-dynamics modeling and RL; it now integrates Microsoft's **RD-Agent** for
   automated factor mining / model optimization. Maps almost exactly onto the
   Quant Research Brain.
2. **MLFinLab** — the system must not merely *find* strategies; it must determine
   whether they are **statistically credible**. Use its concepts for the
   `Hypothesis → Experiment → Validation → Robustness → Promotion` pipeline.
3. **FinRL / FinRL-Meta** — a research avenue for the Strategy Evolution Engine.
   **RL must NOT directly control live trading**; it sits behind the
   research / backtesting / paper-trading firewall.

**Keep the LLM out of the math path** — indicators become a deterministic
feature-generation service:
```text
OHLCV
 ├── TA-Lib: RSI · MACD · ADX · ATR · Bollinger · candlestick patterns
 └── Custom Financial-Brain factors:
      momentum · volatility · liquidity · breadth · factor exposure · regime features
```

**Discipline — do not add all 10 as dependencies:**
- **Reuse directly**: Qlib · MLFinLab concepts/tooling · PyPortfolioOpt / Riskfolio-Lib ·
  TA-Lib · selected ML-factor research code
- **Research references only**: FinRL · FinRL-Meta · Zipline · OptimalPortfolios ·
  ML-Quant-Trading
- Licensing differs substantially per repo — **audit each license and dependency**
  before copying code into a proprietary Financial-Brain.

Architectural conclusion: this is **no longer one trading application**. It is a stack of
specialized scientific/financial subsystems with our own **Financial-Brain control plane,
knowledge graph, memory, Indian-market data layer, agent organization and safety layer**
above them.

Stack at this point:
FINANCIAL BRAIN → {MARKET INTELLIGENCE BRAIN (Fundamentals, Technical, News) →
MARKET WORLD STATE; QUANT RESEARCH BRAIN (Papers, Factors, ML/RL) → HYPOTHESIS ENGINE →
STRATEGY GENOME} → {Qlib, MLFinLab, FinRL} → BACKTEST LAB {Vibe-Trading, NautilusTrader}
→ ROBUSTNESS ENGINE {Walk Forward, Monte Carlo, OOS} → STRATEGY JUDGE → REJECT / PROMOTE
→ PAPER TRADING → LIVE VALIDATION → PORTFOLIO BRAIN {PyPortfolioOpt, Riskfolio-Lib} →
RISK ENGINE → HUMAN APPROVAL → KITE.

### Batch 2 — repos 11–20

| # | Repository | What we take | Financial-Brain module |
|---|---|---|---|
| 11 | [Microsoft Qlib](https://github.com/microsoft/qlib) | AI-driven quant research, alpha discovery, ML models, portfolio construction | 🧠 Quant Brain |
| 12 | [MLFinLab](https://github.com/hudson-and-thames/mlfinlab) | Financial ML, labeling, sampling, backtest statistics, research methodology | 🔬 Research Validation |
| 13 | [Riskfolio-Lib](https://github.com/dcajasn/Riskfolio-Lib) | Advanced portfolio optimization, dozens of risk measures | 🛡️ Risk Brain |
| 14 | [PyPortfolioOpt](https://github.com/robertmartin8/PyPortfolioOpt) | Efficient frontier, Black-Litterman, HRP | 💼 Portfolio Brain |
| 15 | [FinRL](https://github.com/AI4Finance-Foundation/FinRL) | Deep-RL trading research and environments | 🤖 Strategy Evolution |
| 16 | [FinRL-Meta](https://github.com/AI4Finance-Foundation/FinRL-Meta) | Market environments, datasets, RL benchmarks | 🧪 Research Lab |
| 17 | [EntroPy](https://github.com/HeroBlast10/EntroPy) | Multiple-testing controls, factor redundancy, capacity, regime-aware factors, realistic costs | 🔬 Alpha Validation |
| 18 | [quant-factor-research](https://github.com/phdech04/quant-factor-research) | Agentic AI that reads quant papers and writes code to test their factors | 📚 Research → Strategy |
| 19 | [Fundamental-Factor-Mining](https://github.com/benwaldner/Fundamental-Factor-Mining) | 1,000+ fundamental factors, systematic factor testing | 📊 Fundamental Alpha |
| 20 | [cloudQuant/backtrader](https://github.com/cloudQuant/backtrader) | Backtesting/live-trading infrastructure plus MCP/agent workflow | ⚙️ Execution/Backtesting |

Some overlap with batch 1 — **#17–19 are the genuinely new and important ones.**

#### 17. EntroPy → the **Alpha Validation Firewall**
Unusually aligned with the philosophy developed here. It addresses: multiple-testing
problems · factor redundancy · IC/RankIC · IC decay · capacity · transaction costs ·
regime stability · out-of-sample testing · factor risk models · portfolio constraints ·
**Deflated Sharpe** · Reality Check-style testing · experiment configuration and
reproducibility.

Pipeline becomes:
```text
Research Agent -> Hypothesis -> Factor Generator -> Backtest
        |
+------------------------------+
|     ALPHA VALIDATION         |
|  IC / ICIR                   |
|  Multiple Testing            |
|  Redundancy                  |
|  Deflated Sharpe             |
|  Walk Forward                |
|  OOS                         |
|  Transaction Costs           |
|  Capacity                    |
|  Regime Stability            |
+--------------+---------------+
               |
        Strategy Judge
```
**This is mandatory before a strategy may enter paper trading.**

#### 18. Agentic quant research (`phdech04/quant-factor-research`)
An **agentic-AI alpha-discovery loop** where agents read quantitative-finance papers and
generate pandas code to test the factors those papers describe — a small-scale version of
the Financial-Brain research loop. Evolve it into:

RESEARCH DIRECTOR → Literature Agent → {Papers, Books} → Concept Extractor →
Hypothesis Engine → Factor Builder → Code Generator → Backtest Engine →
Statistical Judge → REJECT / PROMOTE.

> **The AI is not allowed to declare itself successful. The validation layer does that.**

#### 19. Fundamental Factor Mining
Systematic testing of **1,000+ fundamental factors** (demonstrated on China A-shares),
including factors derived from financial statements and their transformations.
Adapt to India:

```text
Indian Companies
   |-- P&L
   |-- Balance Sheet
   |-- Cash Flow
   |-- Shareholding
   |-- Promoter Data
   |-- Corporate Actions
   +-- Filings
          v
   Fundamental Factor Factory
     |-- Value
     |-- Quality
     +-- Growth
          v
     Factor Library
          v
   IC / IR / OOS / Cost
```
Goal: **thousands of India-specific fundamental factors.**

#### 20. Backtrader's newer direction
`cloudQuant/backtrader` is not just a conventional backtester — its ecosystem includes a
web platform and an AI/MCP-oriented workflow around research → strategy generation →
backtesting → paper trading → live execution. Worth studying for the **execution
lifecycle**, but **do not run it alongside NautilusTrader** — compare
**NautilusTrader vs Backtrader vs our own execution abstraction** and pick **one** core
execution engine.

#### Bonus find — `quant-research-lab`
A research reference library (not a core dependency) containing: factor research ·
point-in-time handling · event-driven backtesting · statistical arbitrage · options
volatility surfaces · execution algorithms · market microstructure · transaction costs ·
walk-forward validation · ML/regime allocation. Useful when building the derivatives and
execution portions.

### Consolidated stack after 20 repos

```text
                         FINANCIAL-BRAIN
                    +----------+----------+
             MARKET BRAIN             QUANT BRAIN
          +---------+---------+     +-----+-----+
     Fundamental Technical  News  Papers Factors ML/RL
          +---------+---------+     +-----+-----+
                                   Hypothesis Engine
                                          |
                                   Strategy Genome
                                  +-------+--------+
                              Qlib             FinRL
                                  +-------+--------+
                                   BACKTEST LAB
                              +-----------+----------+
                          Vibe-Trading Nautilus  Backtrader
                              +-----------+----------+
                                ALPHA VALIDATION
                             +------------+------------+
                         MLFinLab      EntroPy      Statistics
                             +------------+------------+
                                   STRATEGY JUDGE
                                   +------+------+
                                REJECT        PROMOTE
                                                v
                                         PAPER TRADING
                                                v
                                         PORTFOLIO BRAIN
                                      +---------+---------+
                                 Riskfolio          PyPortfolioOpt
                                      +---------+---------+
                                           RISK ENGINE
                                                v
                                         HUMAN APPROVAL
                                                v
                                               KITE
```

Above all of it:
```text
                         PAPERCLIP
                 CEO / Research Director
          +-----------------+-----------------+
     Research Agents   Quant Agents     Portfolio Agents
          +-----------------+-----------------+
                      FINANCIAL BRAIN
        +-------------------+-------------------+
   ONTOBRICKS          MEMORY/CONTEXT       KNOWLEDGE
   Knowledge Graph      OpenViking/etc.        Base
        +-------------------+-------------------+
                    DECISION / EXPERIENCE
                            v
                       OUTCOME MEMORY
                            v
                      ADAPTATION ENGINE
```

### ⚠ The stated next step (open task)

We are now at **20+ repositories**, and adding repos indefinitely will make the
architecture *worse*, not better. The next useful step is to classify **every**
repository found so far as **BUILD / INTEGRATE / WRAP / STUDY / REJECT**, recording for each:

- license
- GitHub activity
- maturity
- overlap with other choices
- dependencies
- Indian-market suitability
- exactly which code/components we would reuse
- what we should build ourselves instead
- where it sits in Financial-Brain

That produces the **master repository / technology map**, instead of collecting more repos.

---

## 16. FinLLMs — the academic map of LLMs in finance

**Repo:** [adlnlp/FinLLMs](https://github.com/adlnlp/FinLLMs) · ~387 stars ·
**no LICENSE file** (curated list + figures; treat as reference, not code) ·
last updated Aug 2026.

Companion to the survey *"A Survey of Large Language Models in Finance (FinLLMs)"*
([arXiv 2402.02315](https://arxiv.org/abs/2402.02315)), published in *Neural Computing
and Applications* (2025). It contains **no runnable code** — README, figures, paper and
dataset links only.

### Why it matters here
Every other repo in this spec is *machinery*. This one is the **evaluation and
methodology map**: it tells us which financial-NLP tasks are solved, which are not, what
the benchmark datasets are, and how much headroom actually exists. It is the reference
for the Research Brain's *evaluation* discipline and for deciding where a language model
belongs in Financial-Brain at all.

### Evolution: general LMs → financial LMs
- **General-domain**: GPT-1/2/3/4, BERT, T5, ELECTRA, BLOOM, LLaMA, LLaMA2
- **Financial-domain**: FinBERT-19 (ProsusAI), FinBERT-20, FinBERT-21 (IJCAI),
  FLANG (EMNLP 2022), **BloombergGPT**, **FinMA / PIXIU**, **InvestLM**, **FinGPT**
  (AI4Finance — same foundation as FinRL, already in our stack)

### Five techniques (the taxonomy to steer our own model choices)
| Technique | Exemplars |
|---|---|
| Continual pre-training | FinBERT-19 |
| Domain-specific pre-training from scratch | FinBERT-20 |
| Mixed-domain pre-training | FinBERT-21, FLANG |
| Mixed-domain LLM + prompt engineering | BloombergGPT |
| Instruction fine-tuned LLM + prompt engineering | FinMA, InvestLM, FinGPT |

**Implication for us:** pre-training from scratch is off the table; the viable path is
**instruction fine-tuning + prompt engineering on top of a strong general model**, with
small specialist encoders (FinBERT-class) for narrow, high-volume classification jobs
(sentiment tagging of news/filings) where an LLM call per document is wasteful.

### Six benchmark tasks and their datasets
| Task | Datasets |
|---|---|
| Sentiment Analysis (SA) | Financial PhraseBank (FPB), FiQA-SA, SemEval-2017, StockEmotions |
| Text Classification (TC) | Headline, FedNLP, **FOMC (Trillion Dollar Words)**, Banking77 |
| Named Entity Recognition (NER) | FIN, **FiNER-139** (XBRL tagging) |
| Question Answering (QA) | FiQA-QA, **FinQA**, **ConvFinQA**, **TAT-QA**, PACIFIC |
| Stock Movement Prediction (SMP) | StockNet, CIKM18, BigData22 |
| Text Summarization (Summ) | **ECTSum** (earnings-call bullet summarisation), MultiLing 2019 |

Evaluation compares FinPLMs (FLANG), FinLLMs (BloombergGPT, FinMA), general LLMs
(ChatGPT, GPT-4) and task-specific SOTA. Note the survey's own caveat: **FinPLMs were
never run on the harder tasks** (hybrid QA, SMP, summarisation), so those cells are
absent rather than lost.

### Eight advanced tasks
Relation Extraction (**FinRED**) · Event Detection (**EDT** — corporate events for
news-driven trading) · Causality Detection (FinCausal20) · Numerical Reasoning
(FiNER-139, FinQA, ConvFinQA, TAT-QA, PACIFIC) · Structure Recognition (**FinTabNet** —
table extraction) · Multimodal Understanding (**MAEC**, **MONOPOLY** — earnings-call and
policy-conference audio/video) · Machine Translation (MINDS-14, **MultiFin** —
multilingual financial NLP) · Market Forecasting.

### Where each task maps into Financial-Brain
| FinLLM task | Financial-Brain module |
|---|---|
| Sentiment Analysis | Social & Sentiment Intelligence layer |
| Text Classification | Event Intelligence (hawkish/dovish RBI policy ≈ FOMC task) |
| NER / Structure Recognition | Document Intelligence → knowledge-graph entity extraction; table extraction from annual reports |
| QA / Numerical Reasoning | Market Copilot; reasoning over filings and statements |
| Relation Extraction | **Indian Market Knowledge Graph edge construction** (supplier/customer/promoter links) |
| Event Detection | Event Intelligence Engine; news → stock impact |
| Causality Detection | Event → sector → company propagation |
| Summarization (ECTSum) | Earnings-call and filing digests; "what changed since yesterday" |
| Multimodal (MAEC, MONOPOLY) | Earnings-call audio analysis (management tone) |
| Machine Translation (MultiFin) | **Multilingual Indian financial documents** — the differentiator already flagged for Sarvam |
| Stock Movement Prediction | ⚠ see below |

### ⚠ The most important lesson from this repo
SMP is a benchmark task with **weak, contested effect sizes** across StockNet / CIKM18 /
BigData22 — and the survey's comparison shows no model, FinLLM or otherwise, achieving
anything resembling a reliable edge. This is direct empirical support for the rule
already in §14:

> **The LLM is not the alpha source.** Use FinLLM capability for *reading* — extraction,
> classification, relation-building, summarisation, QA — and leave *prediction* to the
> quant stack behind the Alpha Validation Firewall (§15).

### The India gap this exposes
Every dataset above is **English and almost entirely US/EU-centric** (FPB, FiQA, FOMC,
FinQA, ECTSum, StockNet are all US). There is **no Indian equivalent** in the benchmark
set. Consequences:

1. We cannot evaluate Indian financial NLP against any published baseline — we must
   **build our own evaluation set** (a labelled corpus of NSE/BSE announcements, Indian
   earnings-call transcripts, RBI policy statements).
2. **RBI policy statements are the Indian FOMC task** — hawkish/dovish classification
   transfers conceptually and is worth building as our first labelled dataset.
3. **MultiFin / MINDS-14** are the only multilingual footholds; Indian regional-language
   financial text is essentially unbenchmarked. That is an open research gap *and* our
   stated differentiator.
4. Indian filings are largely in English (helpful), but earnings calls carry heavy
   accent/code-switching that off-the-shelf ASR handles worse than US calls — a real
   cost on the MAEC-style multimodal path.

### Classification
**STUDY** — not INTEGRATE. No code, no license. Use it for:
- choosing model architecture per task (encoder vs LLM)
- designing the Research Brain's evaluation harness
- the reading list of datasets to mirror for India
- resisting the temptation to make the LLM predict prices

Also worth pulling from the ecosystem it points at: **FinGPT** (AI4Finance — instruction
tuning, already adjacent to FinRL in our stack) and **PIXIU/FLARE** (the leaderboard
harness), both of which *are* runnable and permissively adjacent to what we already use.

Related venues to track: FNP, FinNLP, ECONLP, AAAI AI-for-Financial-Services bridge,
MUFFIN, KDF.

---

## 17. From a design dossier to a Financial Brain — missing operating contracts

### The central correction

The project does **not** become a Financial Brain by adding more agents, repositories,
or model providers. It becomes one by operating a reliable, measurable, and governed
closed loop:

```text
Observe -> Verify -> Model the market -> Form a thesis -> Size risk
   -> Decide -> Record -> Observe outcome -> Diagnose -> Improve
```

The durable intellectual property is therefore:

```text
Evidence -> Thesis -> Decision -> Outcome -> Postmortem -> Calibrated future judgement
```

It is neither a chatbot, a RAG system, nor a swarm of agents. The LLM may help to
investigate, extract, summarise and argue; deterministic data, validation, policy, and
risk controls decide what may be proposed or executed.

### 17.1 Product charter comes before architecture

Choose a single initial user and job before building further:

- long-term Indian-equity investor;
- active swing trader;
- registered advisor/research team; or
- institutional investor.

These are different products. They imply different decision horizons, data latency,
portfolio constraints, evaluation metrics, user experience, compliance requirements,
and acceptable automation. The recommended first user is a **self-directed,
long-term Indian-equity investor**. The first job is: *understand what changed in my
portfolio/watchlist, why it matters, and whether any recorded thesis has strengthened
or weakened.*

Do not market the initial product as a stock-tip engine. If the service provides
buy/sell/hold calls, targets, stop losses, model portfolios, or personalised investment
recommendations to others, obtain specialist SEBI legal advice and design the product,
disclosures, records, and operating model accordingly.

### 17.2 Canonical, point-in-time market data model

Before agents, establish a canonical security master and time-aware data contracts.
One company/security must resolve across NSE, BSE, Kite, Screener, Moneycontrol, and
company filings, including:

- ISIN, exchange identifiers, symbols, company and group identity;
- corporate actions, mergers, splits, bonuses, dividends, symbol changes, suspensions,
  and delistings;
- adjusted and unadjusted OHLCV, with an explicit adjustment policy;
- financial-statement period, filing date, publication time, revisions/restatements,
  and source document;
- instrument universe membership at each historic point; and
- entitlement, license, freshness, and quality metadata for every feed.

Every fact must distinguish **event time** (when it occurred), **publication time**
(when it became public), **ingestion time** (when Financial-Brain saw it), and
**effective/as-of time** (the latest information that could validly have informed a
decision). This is indispensable for preventing look-ahead and survivorship bias.

### 17.3 Evidence ledger and provenance

Citation is not enough. Store an immutable evidence ledger for numbers, statements,
extracted claims, and derived conclusions:

```text
evidence_id -> source URI/document -> source tier -> content hash -> retrieval time
            -> event/publication time -> extraction/transformation -> confidence
            -> data-quality status -> world-state version -> decisions that used it
```

Claims must never silently overwrite their source. A revised result, corrected filing,
or reclassified entity creates a new version and retains the old one. This makes every
recommendation reproducible and auditable.

### 17.4 Market World State

Agents must reason from a versioned **Market World State**, not independently browse
the web and improvise. A world-state snapshot includes:

- price, liquidity, volatility, breadth, index and sector state;
- company fundamentals, filings, events, and disclosure status;
- macro/regime variables and their data-as-of time;
- known relationships in the company/sector/policy graph;
- portfolio positions, cash, exposures, constraints, and open orders;
- unresolved hypotheses, thesis invalidations, anomalies, and missing data; and
- a complete evidence set and data-quality assessment.

The Orchestrator may request research that produces candidate evidence. Only verified,
typed evidence updates the world state. Every decision references one immutable
world-state version.

### 17.5 Separate knowledge, context, and experience in concrete stores

The three-store principle must be implemented, not merely named:

| Store | Purpose | Examples |
|---|---|---|
| Knowledge | Slow-changing, sourced facts and relationships | ownership, products, sector, supplier links, policy concepts |
| Context | Current, time-bounded market world state | today's price, regime, results, event, portfolio exposure |
| Experience | Decision/outcome history | prior thesis, observed return, attribution, postmortem, calibration |

No vector database or graph replaces the decision database. The decision database is
the system's empirical memory; the knowledge graph represents relationships, and
retrieval helps agents find evidence.

### 17.6 Decision contract and state machine

Make every recommendation a structured, immutable decision record rather than prose:

```text
decision_id, instrument, universe, horizon, action, thesis,
supporting_evidence, contrary_evidence, primary_uncertainty,
world_state_version, expected payoff/scenarios, invalidation conditions,
entry/exit logic, sizing/risk budget, portfolio impact, approver,
execution status, outcome window, postmortem status
```

Use a strict lifecycle:

```text
DRAFT -> EVIDENCE VERIFIED -> RISK REVIEWED -> PAPER CANDIDATE
      -> HUMAN-APPROVED -> PROPOSED TO BROKER -> EXECUTED
      -> OUTCOME MEASURED -> POSTMORTEM COMPLETE
```

Only the human-approval/execution policy may advance a live-trade proposal. An LLM,
web page, document, social post, or agent workflow cannot bypass this state machine.

### 17.7 Two decision lanes, not one

The existing statement "no BUY without a backtest" applies to deterministic,
repeatable quantitative strategies, not cleanly to every single-company fundamental
thesis. Keep two explicit lanes:

1. **Quantitative strategy lane** — versioned code; point-in-time universe and data;
   realistic costs, slippage and liquidity; train/test separation; walk-forward,
   out-of-sample, robustness, and capacity validation; paper trading before promotion.
2. **Fundamental/discretionary research lane** — source quality; falsifiable thesis;
   comparable historical case studies where possible; scenario/risk analysis;
   portfolio constraints; continuous invalidation monitoring; outcome postmortem.

Both lanes use the same evidence ledger, world-state, decision record, risk gate, and
outcome store. Neither lane is allowed to change live trading rules directly.

### 17.8 Scores must be calibrated decision aids, not predictions

An AI/entry score is meaningful only if it declares:

- the instrument universe and time horizon;
- the benchmark and definition of success;
- point-in-time features and missing-data policy;
- transaction-cost, liquidity, and capacity assumptions;
- historical calibration/reliability by score band, regime, and market-cap segment; and
- uncertainty, data quality, and disagreement separately from the score.

Never show a number such as `82/100` as a price-prediction probability. It is a
decision aid whose usefulness must be measured against a transparent benchmark.

### 17.9 Outcome attribution and controlled learning

Raw return is not learning. For each completed decision, attribute the result where
possible to market beta, sector move, factor exposure, earnings surprise, valuation
change, timing, execution cost, thesis error, risk-rule error, or luck. Track accuracy
by horizon, market regime, sector, market-cap band, strategy, and decision type.

The adaptation loop may create a *candidate* improvement only through:

```text
Observed outcome -> attribution -> hypothesis -> versioned experiment
-> validation -> paper trade -> risk committee -> human-approved promotion
```

It must support rollback, preserve old versions, and never make a silent prompt,
model, score, or live-rule change.

### 17.10 Evaluation harness and acceptance criteria

Build evaluation before broad autonomy. Maintain labelled Indian data and tests for:

- entity resolution and corporate-action accuracy;
- filing/event extraction, classification, and timeliness;
- citation correctness, claim-to-evidence support, and contradiction detection;
- data freshness, missing-data handling, and source-tier precedence;
- regime classification stability;
- thesis-invalidation alert recall and false-positive rate;
- decision calibration and performance versus declared benchmarks;
- quant backtest reproducibility, point-in-time integrity, costs, and leakage checks;
- prompt-injection resistance and tool-permission enforcement; and
- portfolio-risk and order-policy rejection tests.

The governing measure is not "did the AI sound insightful?" It is: *did this system
improve evidence quality, decision discipline, calibrated judgement, and risk-adjusted
outcomes relative to the stated benchmark?*

### 17.11 Security, data, and operational governance

Every browser page, PDF, social post, transcript, external tool result, and user upload
is untrusted content. The harness must provide prompt-injection detection/isolation,
credential vaulting, secret redaction, tool allowlists, least-privilege access,
transaction scopes, immutable audit logs, sandboxed code execution, and data
exfiltration controls.

Production readiness also requires explicit data entitlements and retention rules;
source licensing; privacy/consent policy for portfolio data and uploaded documents;
observability; incident response; backups/disaster recovery; and legal/compliance
review of recommendation and execution features.

### 17.12 Minimum viable Financial Brain

The first real vertical slice should be deliberately narrow:

1. Official NSE/BSE disclosures plus licensed or permitted EOD price data and one
   canonical security master.
2. A versioned watchlist/portfolio world state.
3. A cited **What changed since yesterday?** briefing.
4. A company thesis card showing supporting evidence, contrary evidence, uncertainty,
   invalidation conditions, and data quality.
5. Immutable decisions, alerts, outcome records, and postmortems.
6. Portfolio risk/concentration analysis and paper-only proposed actions.

Only after that slice proves reliable should the project add real-time data, broad
social ingestion, complex graph inference, agent organisations, strategy discovery,
and human-approved broker execution.

### What this project does not need initially

- twenty integrated repositories;
- autonomous execution or reinforcement learning;
- a Bloomberg-scale terminal;
- a large multi-agent hierarchy;
- a knowledge graph for every possible relationship; or
- a promise to predict winning stocks.

The first product should be named and evaluated as an **Indian Equity Research &
Portfolio Copilot**. Its job is to make the investor more informed, consistent,
auditable, and risk-aware. A Financial Brain emerges only after repeated evidence,
decisions, and measured outcomes demonstrate calibrated improvement.
