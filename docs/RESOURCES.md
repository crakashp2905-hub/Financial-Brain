# Financial-Brain — Resource Register

Every external resource the project has evaluated: code, data, models, datasets,
benchmarks and reference material. One row per resource, with what it gives us, what it
costs us, and what we have decided to do about it.

Design decisions live in [ARCHITECTURE.md](ARCHITECTURE.md).
India-specific constraints live in [FEASIBILITY-INDIA.md](FEASIBILITY-INDIA.md).

**Last verified:** 2026-09-14 (licenses and star counts read from the GitHub API on that
date; anything marked *verify* was not confirmable programmatically).

---

## How to read this

**Verdict vocabulary** — every resource carries exactly one:

| Verdict | Meaning |
|---|---|
| **INTEGRATE** | Becomes a runtime dependency of Financial-Brain |
| **WRAP** | Used, but only behind our own interface so it can be swapped out |
| **STUDY** | Read it, copy ideas, do not depend on it |
| **REFERENCE** | Data, papers or docs consulted; nothing to integrate |
| **BUILD** | We build this ourselves; listed here because a resource tempted us not to |
| **REJECT** | Evaluated and declined, with reason |

**Licence risk key:**

| Flag | Meaning |
|---|---|
| 🟢 | Permissive (MIT, BSD, Apache-2.0) — safe to integrate |
| 🟡 | Weak copyleft (LGPL) or unclear — usable with care |
| 🔴 | Strong copyleft (AGPL/GPL) or proprietary — blocks or constrains commercial use |
| ⚪ | No licence file — legally *all rights reserved*; cannot copy code |

---

## ⚠ Two corrections to earlier assumptions

Verifying licences against the GitHub API contradicted two load-bearing assumptions
carried through the original design:

1. **OpenBB is AGPL-3.0**, not permissive. It was designated the data-abstraction core
   that everything else sits on. If Financial-Brain is ever offered as a service, AGPL
   §13 reaches it. **OpenBB is downgraded from INTEGRATE to WRAP** — it must sit behind
   our own provider interface, exactly as OpenViking already does.
2. **MLFinLab is not open source.** The repository carries a Hudson & Thames
   "Copyright Protection Notice and Licensing Agreement", not a permissive licence.
   It was listed as "reuse directly". **Downgraded to STUDY** — use the *methods* from
   the accompanying book and papers; do not vendor the code.

Consequence: the only genuinely permissive pieces of our proposed core are
Vibe-Trading, Qlib, skfolio and the portfolio-optimisation libraries. The data plane and
the memory layer both need our own abstraction boundary.

---

## 1. Master verdict map

The classification the design work kept deferring. Every resource, one line.

| Resource | Category | Licence | Verdict | Financial-Brain role |
|---|---|---|---|---|
| [Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) | Platform | 🟢 MIT | **INTEGRATE** | Quant research engine, skills, backtesting |
| [Qlib](https://github.com/microsoft/qlib) | Quant | 🟢 MIT | **INTEGRATE** | Factor mining, ML pipeline, alpha research |
| [skfolio](https://github.com/skfolio/skfolio) | Portfolio | 🟢 BSD-3 | **INTEGRATE** | Portfolio optimisation |
| [Riskfolio-Lib](https://github.com/dcajasn/Riskfolio-Lib) | Portfolio | 🟢 BSD-3 | **INTEGRATE** | Risk measures, constrained optimisation |
| [PyPortfolioOpt](https://github.com/robertmartin8/PyPortfolioOpt) | Portfolio | 🟢 MIT | **INTEGRATE** | Efficient frontier, Black-Litterman, HRP |
| [TA-Lib (python)](https://github.com/TA-Lib/ta-lib-python) | Quant | 🟢 BSD-2 | **INTEGRATE** | Deterministic indicator service (Phase 2) |
| [Docling](https://github.com/docling-project/docling) | Documents | 🟢 MIT | **INTEGRATE** | Filing/PDF/table/XBRL parsing — Phase 1–2 |
| [Dagster](https://docs.dagster.io/) | Orchestration | 🟢 Apache-2.0 | **DEFER** | Only when idempotent jobs + a scheduler stop being enough |
| [TradingAgents](https://github.com/TauricResearch/TradingAgents) | Agents | 🟢 Apache-2.0 | **INTEGRATE** | Investment-committee graph |
| [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) | Execution | 🟡 LGPL-3.0 | **WRAP** | Event-driven backtest + execution core |
| [OpenBB](https://github.com/OpenBB-finance/OpenBB) | Data | 🔴 AGPL-3.0 | **WRAP** | Data abstraction — behind our provider API |
| [OpenViking](https://github.com/volcengine/OpenViking) | Memory | 🔴 AGPL-3.0 | **WRAP** | Context OS — behind replaceable interface |
| [Browser Use](https://github.com/browser-use/browser-use) | Acquisition | 🟢 MIT | **WRAP** | Last-mile research fallback, sandboxed |
| [Fincept Terminal](https://github.com/Fincept-Corporation/FinceptTerminal) | Terminal | 🔴 AGPL-3.0 | **STUDY** | UI/service architecture reference |
| [MLFinLab](https://github.com/hudson-and-thames/mlfinlab) | Quant science | 🔴 Proprietary | **STUDY** | Financial-ML methodology only |
| [EntroPy](https://github.com/HeroBlast10/EntroPy) | Validation | 🟢 MIT | **STUDY → INTEGRATE** | Alpha Validation Firewall blueprint |
| [FinRL](https://github.com/AI4Finance-Foundation/FinRL) | RL | 🟢 MIT | **STUDY** | Strategy-evolution research only |
| [FinRL-Meta](https://github.com/AI4Finance-Foundation/FinRL-Meta) | RL | 🟢 MIT | **STUDY** | RL environments/benchmarks |
| [FinGPT](https://github.com/AI4Finance-Foundation/FinGPT) | Model | 🟢 MIT | **STUDY** | Instruction-tuning reference |
| [PIXIU / FLARE](https://github.com/chancefocus/PIXIU) | Eval | 🟢 MIT | **STUDY** | Eval-harness pattern for our India benchmark |
| [Zipline-Reloaded](https://github.com/stefan-jansen/zipline-reloaded) | Backtest | 🟢 Apache-2.0 | **STUDY** | Reference; superseded by Nautilus |
| [cloudQuant/backtrader](https://github.com/cloudQuant/backtrader) | Backtest | 🔴 GPL-3.0 | **STUDY** | Execution-lifecycle ideas only |
| [OptimalPortfolios](https://github.com/ArturSepp/OptimalPortfolios) | Portfolio | 🟢 MIT | **STUDY** | Institutional construction patterns |
| [ml-quant-trading](https://github.com/initial-d/ml-quant-trading) | Factors | 🟢 MIT | **STUDY** | 213-factor pipeline reference |
| [quant-factor-research](https://github.com/phdech04/quant-factor-research) | Agents | ⚪ None | **STUDY** | Paper→code alpha loop, idea only |
| [Fundamental-Factor-Mining](https://github.com/benwaldner/Fundamental-Factor-Mining) | Factors | ⚪ None | **STUDY** | Blocked in India by PIT data |
| [Awesome Harness Engineering](https://github.com/ai-boost/awesome-harness-engineering) | Method | ⚪ verify | **REFERENCE** | Harness design manual |
| [Scientific Agent Skills](https://github.com/K-Dense-AI/scientific-agent-skills) | Skills | 🟢 MIT | **STUDY** | ~12 methodology skills only |
| [Anthropic Cybersecurity Skills](https://github.com/mukul975/Anthropic-Cybersecurity-Skills) | Security | 🟢 Apache-2.0 | **STUDY** | Agent Security Brain reference |
| [Agent Memory](https://github.com/rohitg00/agentmemory) | Memory | 🟢 Apache-2.0 | **STUDY** | Coding-agent memory ≠ financial memory |
| [Diagram Design](https://github.com/cathrynlavery/diagram-design) | Docs | 🟢 MIT | **INTEGRATE (dev)** | Architecture diagrams, not runtime |
| [schematics](https://github.com/joehaddad2000/schematics) | Docs | ⚪ None | **REFERENCE** | Alternative diagram skill |
| [FinLLMs survey](https://github.com/adlnlp/FinLLMs) | Research | ⚪ None | **REFERENCE** | Task taxonomy, benchmark map |
| Paperclip | Orchestration | ⚪ verify | **STUDY** | Agent control plane — see §2.7 |
| OntoBricks | Knowledge | ⚪ verify | **STUDY** | Ontology/knowledge graph |
| Hummingbot | Execution | 🟢 Apache-2.0 | **REJECT** | Crypto-market-making focus; not our need |
| Kronos | Models | ⚪ verify | **REJECT** | Deferred; no clear role yet |
| [quant-research-lab] | Reference | ⚪ verify | **REFERENCE** | Derivatives/microstructure modules |

**Resources we build ourselves** (§4 of ARCHITECTURE.md) — listed so nobody imports a
substitute: Indian security master · PIT fundamentals store · India Implementability
Gate · Indian Market Knowledge Graph · Investment Constitution · Decision/Thesis memory ·
Outcome attribution & calibration · Market Regime Brain · Indian cost model ·
India evaluation benchmark.

---

## 2. Platforms and frameworks

### 2.1 Vibe-Trading — **INTEGRATE**
`HKUDS/Vibe-Trading` · 🟢 MIT · ~33.4k ★

The closest existing thing to our Quant Research Brain. ReAct agent core, **69 finance
skills**, 29 multi-agent swarm presets, DAG orchestration, natural-language backtesting,
factor IC/IR analysis, quantile backtesting, technical pattern analysis, options/Greeks,
portfolio optimisation, Monte Carlo, bootstrap confidence intervals, walk-forward
validation, strategy generation, report generation, MCP interface, persistent run
memory/artifacts, strategy export (Pine Script, TDX, MQL5). Its **Hypothesis Registry**
(Hypothesis → Deterministic Backtest → Evidence Report, with reproducible run cards and
citations) is the piece we were about to rebuild.

- **Keep**: strategy framework, backtesting, factor research, validation, skills, MCP
- **Extend**: NSE/BSE, Kite, Indian corporate data, Indian fundamentals, Indian costs
- **Replace/add**: research memory, knowledge graph, literature ingestion, strategy
  genome, regime intelligence, long-term adaptation
- India note: later releases/forks claim first-class NSE/BSE backtesting and a
  fundamental factor layer — *verify before relying on it*

### 2.2 TradingAgents — **INTEGRATE**
`TauricResearch/TradingAgents` · 🟢 Apache-2.0 · ~105.8k ★

LangGraph multi-agent skeleton: Fundamental / Sentiment / News / Technical analysts →
Bull & Bear researchers → Trader → Risk Management → Portfolio Manager. Has structured
outputs, persistent decision logs, checkpointing, multiple model providers.

Use as the committee graph; do **not** fork-and-swap US data for Indian data. Missing
piece we must add: a **Market Regime Agent above the stock analysts**. The project
describes itself as research-oriented and warns results vary by model, data and period.

### 2.3 Fincept Terminal — **STUDY**
`Fincept-Corporation/FinceptTerminal` · 🔴 **AGPL-3.0-or-later** · ~31.6k ★

Native C++/Qt with embedded Python analytics: 54 screens, ~50 services, 40+ MCP tools,
16 equity/F&O brokers plus crypto exchanges; bounded contexts for Markets, News,
Economics, Trading, Portfolio, Derivatives, Predictions, Agents, AI Chat; workflow/node
editor, paper trading, backtesting, quant modules.

The best available reference for terminal architecture and for the MCP tool surface we
should expose. **Do not fork into anything commercial** — modified distribution or SaaS
triggers AGPL source-release obligations, and the Enterprise edition is proprietary.
Worth stealing conceptually: the **visual workflow builder** (drag nodes: NSE DATA →
Market Regime → Screen Stocks → Fundamental/Technical → Bull/Bear → Risk Check → Paper
Trade), which makes the system useful even with the LLM switched off.

### 2.4 OpenBB — **WRAP** *(downgraded)*
`OpenBB-finance/OpenBB` · 🔴 **AGPL-3.0** · ~73k ★

Open Data Platform unifying proprietary, licensed and public data behind Python, REST,
MCP and app interfaces. Correct *shape* for our data plane — wrong licence to sit at the
core of a commercial product.

Treat as one provider implementation behind `financial_brain.data.Provider`. It is a
data **abstraction**, never the Indian data solution: NSE, BSE, Kite, filings, RBI,
SEBI, economic data, news, Screener and Moneycontrol providers all have to be built or
adapted, each subject to its own terms.

### 2.5 NautilusTrader — **WRAP**
`nautechsystems/nautilus_trader` · 🟡 LGPL-3.0 · ~28.9k ★

Event-driven backtesting and live execution. LGPL is workable if we link rather than
modify, but keep it behind our own execution interface.

**Decision required:** NautilusTrader vs cloudQuant/backtrader vs our own abstraction —
pick exactly **one** core execution engine. Recommendation: Nautilus (backtrader is
GPL-3.0, which is worse for us).

### 2.6 Hummingbot — **REJECT**
`hummingbot/hummingbot` · 🟢 Apache-2.0 · ~20k ★

Algorithmic execution infrastructure, but oriented to crypto market making. Our execution
need is low-frequency, human-approved, Kite-routed Indian equity orders. Nothing here
justifies the integration cost.

### 2.7 Paperclip — **STUDY**
*Licence unverified — confirm before any integration.*

Control plane for teams of AI agents: roles, reporting relationships, budgets, goals,
tasks, delegation, auditability; agents woken by schedules, assignments, mentions or
manual invocation, running in short heartbeat cycles. Would replace building an agent
registry, task manager, hierarchy, budgets, delegation, heartbeats, status, approvals
and audit logs ourselves.

Compelling for the "agents create tasks for other agents" research-organisation model
(Literature Researcher finds a paper → task for Quant Researcher → task for Strategy
Builder → task for Backtest Agent → task for Strategy Judge). **But** per
[FEASIBILITY-INDIA.md](FEASIBILITY-INDIA.md) and §17 of the architecture, a large agent
hierarchy is explicitly *not* needed for the first product. Park it.

### 2.8 OntoBricks — **STUDY**
*Databricks Labs; licence unverified.*

Turns Databricks/Unity Catalog data into a living knowledge graph: OWL/RDFS ontologies,
R2RML mappings, materialised triples, reasoning, SHACL validation, GraphQL, and **MCP
access for agents**. The MCP interface is the interesting part — it exposes the graph
directly to agents.

Caveat: it presumes Databricks. Our knowledge graph does not have to. Take the ontology
modelling approach; evaluate the runtime separately.

---

## 3. Quantitative research and validation

### 3.1 Qlib — **INTEGRATE**
`microsoft/qlib` · 🟢 MIT · ~48.6k ★

AI-oriented quant platform spanning **alpha discovery → risk modelling → portfolio
optimisation → execution**, with ML, market-dynamics modelling and RL; integrates
Microsoft's **RD-Agent** for automated factor mining and model optimisation. The closest
match to the Quant Research Brain, and permissively licensed. The scientific core.

### 3.2 EntroPy — **STUDY → INTEGRATE**
`HeroBlast10/EntroPy` · 🟢 MIT · new/low-star

Small and unproven, but it is the only resource that directly implements the **Alpha
Validation Firewall**: multiple-testing controls, factor redundancy, IC/RankIC, IC decay,
capacity, transaction costs, regime stability, out-of-sample testing, factor risk models,
portfolio constraints, **Deflated Sharpe**, Reality-Check-style testing, experiment
configuration and reproducibility.

Adopt the *design*; vendor selectively given its maturity. This gate is mandatory before
any strategy reaches paper trading, and it matters more in India than in the US because
the smaller universe and shorter clean history make false positives likelier.

### 3.3 MLFinLab — **STUDY** *(downgraded)*
`hudson-and-thames/mlfinlab` · 🔴 **Proprietary** (Hudson & Thames licensing agreement)

Financial-ML methodology: labelling, sampling, backtest statistics, research rigour.
**Not open source — do not vendor the code.** Use the published methods (the
*Advances in Financial Machine Learning* material it implements) for the
`Hypothesis → Experiment → Validation → Robustness → Promotion` pipeline.

### 3.4 Portfolio and risk libraries — **INTEGRATE**
| Library | Licence | Gives us |
|---|---|---|
| [skfolio](https://github.com/skfolio/skfolio) | 🟢 BSD-3 | sklearn-style portfolio optimisation |
| [Riskfolio-Lib](https://github.com/dcajasn/Riskfolio-Lib) | 🟢 BSD-3 | dozens of risk measures, constrained optimisation |
| [PyPortfolioOpt](https://github.com/robertmartin8/PyPortfolioOpt) | 🟢 MIT | efficient frontier, Black-Litterman, HRP |
| [OptimalPortfolios](https://github.com/ArturSepp/OptimalPortfolios) | 🟢 MIT | *(STUDY)* institutional construction, rolling backtests |

Pick one primary (skfolio or Riskfolio-Lib) and use the others for cross-checking, not
as parallel dependencies.

### 3.5 TA-Lib — **INTEGRATE**
`TA-Lib/ta-lib-python` · 🟢 BSD-2 · ~12.2k ★

150+ indicators and candlestick patterns. Its real architectural job is to **keep the
LLM out of the mathematics path** — indicators become a deterministic
feature-generation service:

```
OHLCV
 ├── TA-Lib: RSI · MACD · ADX · ATR · Bollinger · candlestick patterns
 └── Financial-Brain factors: momentum · volatility · liquidity · breadth ·
     factor exposure · regime features
```

### 3.6 Factor-research references — **STUDY**
| Resource | Licence | Note |
|---|---|---|
| [ml-quant-trading](https://github.com/initial-d/ml-quant-trading) | 🟢 MIT | 213 factors, bias-aware processing, cost-aware backtesting |
| [Fundamental-Factor-Mining](https://github.com/benwaldner/Fundamental-Factor-Mining) | ⚪ None | 1,000+ fundamental factors on China A-shares |
| [quant-factor-research](https://github.com/phdech04/quant-factor-research) | ⚪ None | agentic loop: agents read papers, write pandas to test factors |
| quant-research-lab | ⚪ verify | factor research, point-in-time handling, event-driven backtesting, stat arb, options vol surfaces, execution algos, microstructure, costs, walk-forward, ML/regime allocation |

⚠ **Fundamental-Factor-Mining is blocked in India** by the absence of point-in-time
fundamentals — see [FEASIBILITY-INDIA.md §2.1](FEASIBILITY-INDIA.md). The agentic
paper→code loop in `quant-factor-research` is the one to emulate, with our own
India Implementability Gate in front of it and the Alpha Validation Firewall behind it.

Governing rule from that loop: **the AI is not allowed to declare itself successful.
The validation layer does that.**

### 3.6a Shared 2026-09-19 — assessed

| Resource | Licence | Verdict | Where it fits |
|---|---|---|---|
| [TradingAgents](https://github.com/TauricResearch/TradingAgents) ([paper](https://arxiv.org/abs/2412.20138)) | 🟢 Apache-2.0 | **INTEGRATE** (already, §2.2) | Committee (C16) template: analysts → bull/bear debate → trader → risk. Here it runs on the ADR-0002 router: local models do analyst legwork over Tier-0 facts, a frontier model chairs. |
| [HARLF](https://github.com/franjgs/llm-rl-finance-trader) ([paper](https://arxiv.org/abs/2507.18560)) | 🟢 MIT (5★) | **STUDY** | Hierarchical RL over LLM sentiment for allocation. Its sentiment input is FinBERT, which we measured at macro-F1 0.45 on Indian filings - any RL layer would inherit that. RL stays STUDY (§3.7); a policy would have to pass the firewall like any factor. |
| [Automate Strategy Finding with LLM](https://github.com/kouzhizhuo/Automate-Strategy-Finding-with-LLM-in-Quant-investment) ([paper](https://arxiv.org/abs/2409.06289), EMNLP 2025) | 🔴 no licence | **STUDY (method only)** | LLM proposes alpha formulas, multi-agent filtering. Adopt the loop, not the code: every generated factor is registered (`fb hypothesis`) and every test is a firewall trial - generation at scale is exactly where multiple-testing control matters. |
| [nburgessx/QuantResearch](https://github.com/nburgessx/QuantResearch) | 🔴 no licence | **REFERENCE** | Derivatives-pricing notes (bond futures, LSMC, HJM). No factor-zoo or impact code found. |
| [letianzj/QuantResearch](https://github.com/letianzj/QuantResearch) | 🟢 MIT (3k★, 2023) | **STUDY** | Strategy notebooks incl. a volume-factor Alphalens study. |
| Metaorder impact (square-root law: Almgren et al. 2005; Toth et al. 2011) | public science | **ADOPTED** | `CostModel.sqrt_impact` / `round_trip_sized`: impact = Y·σ·√(Q/V); the Implementability Gate now prices a book's real orders instead of a flat bucket. |
| Factor zoo replication (Hou-Xue-Zhang 2020; Jensen-Kelly-Pedersen 2023) | public science | **PLANNED** | Price/volume factors are computable now (features f1 has 9); accounting factors need fundamentals, not yet ingested. Replicate as pre-registered hypotheses, never as a mined batch. |

### 3.7 Reinforcement learning — **STUDY only**
`FinRL` 🟢 MIT ~16.3k ★ · `FinRL-Meta` 🟢 MIT ~1.9k ★

DRL trading framework plus market environments, datasets and benchmarks. A research
avenue for the Strategy Evolution Engine. **RL must never control live trading** — it
sits behind the research / backtesting / paper-trading firewall like everything else.

### 3.8 Backtesting alternatives — **STUDY**
`Zipline-Reloaded` 🟢 Apache-2.0 (event-driven, well understood, superseded here by
Nautilus) · `cloudQuant/backtrader` 🔴 GPL-3.0 (interesting for its AI/MCP workflow
around research → strategy generation → backtesting → paper → live, but the licence and
the single-execution-engine rule both argue against it).

---

## 4. Agent infrastructure

### 4.1 Awesome Harness Engineering — **REFERENCE**
`ai-boost/awesome-harness-engineering` · licence unverified · ~4.2k ★

Not a dependency — the **design manual**. Organises agent reliability around context &
memory, tool design, constraints, safe autonomy, specs/workflows, orchestration,
evaluation, observability and benchmarks. (Related: `templates/AGENTS.md`; Cowork Forge,
an MIT multi-agent development workflow with staged pipeline.)

Its single most important application here: the harness, not the model, enforces order
validation.

```
Agent → Proposed Order → Risk Validator → Exposure Validator → Position Validator
      → Market-state Validator → Compliance/Policy Validator
      → Human approval / execution policy → Broker
```

### 4.2 OpenViking — **WRAP**
`volcengine/OpenViking` · 🔴 AGPL-3.0 · ~37.2k ★

Context database where memories, resources and skills live in one virtual filesystem
(`viking://`), with hierarchical L0/L1/L2 context layers, dual-layer storage (AGFS
content store — local/S3/memory — plus a vector index with a single source of truth),
CLI/SDK/HTTP interfaces, and automatic extraction of 6-category memories (profile,
preferences, entities, events, cases, patterns). Prerequisites: Python ≥3.10, Go ≥1.22
for some components.

Hierarchical retrieval is exactly right for research: L0 "Momentum research" →
L1 {academic evidence, Indian-market evidence, factor construction, regime effects,
transaction costs} → L2 {specific papers, equations, datasets, backtests}. Beats loading
10,000 documents.

AGPL ⇒ keep behind a replaceable memory interface until the licensing architecture is
settled. **Open question: does OpenViking replace part of our memory architecture, or
are we adding a third memory system?** Decide before integrating.

### 4.3 Agent Memory — **STUDY**
`rohitg00/agentmemory` · 🟢 Apache-2.0 · ~28.4k ★

Persistent memory for coding agents: confidence, lifecycle, knowledge graphs, hybrid
search, MCP interfaces; ~95.2% retrieval recall on LongMemEval-S with ~92% fewer input
tokens than full context; no external databases.

**Coding-agent memory ≠ financial memory.** We need company, strategy, research,
decision, portfolio, agent, outcome, market-regime and user-preference memory — and the
decision database is not a vector store. Take the lifecycle/confidence ideas, not the
substitution.

### 4.4 Browser Use — **WRAP**
`browser-use/browser-use` · 🟢 MIT · ~114.6k ★

Agents driving real browsers. Strict hierarchy: **API → MCP/data connector → Browser
Use**, last-mile only. Good for: research agent → company site → investor presentation →
PDF → extraction → knowledge graph. Never for high-frequency market data.

Introduces prompt-injection, credential and exfiltration risk — must sit behind the
hardened harness with a tool allowlist and sandboxing.

### 4.5 Scientific Agent Skills — **STUDY**
`K-Dense-AI/scientific-agent-skills` · 🟢 MIT · ~44.9k ★

~160+ research skills across scientific databases, statistical workflows, time-series
forecasting and scientific ML. **Do not install all of them** — cancer genomics, drug
discovery, molecular dynamics and RNA velocity are noise for us.

Keep/adapt ~12: statistical analysis, time-series analysis, forecasting, Bayesian
methods, hypothesis testing, experimental design, data analysis, scientific literature
search, citation/provenance, ML experimentation, visualisation, reproducible research.

Then build **our own financial skills**: `factor-research`, `factor-ic-ir`,
`momentum-analysis`, `value-analysis`, `mean-reversion`, `volatility`,
`portfolio-construction`, `transaction-cost-analysis`, `walk-forward-validation`,
`monte-carlo`, `regime-detection`, `event-study`, `backtesting`,
`survivorship-bias-check`, `lookahead-bias-check`, `strategy-postmortem`.

> **Don't build one giant Quant Agent. Build a library of procedural financial skills.**

⚠ Skills execute code and modify agent behaviour — review before installing.

### 4.6 Anthropic Cybersecurity Skills — **STUDY**
`mukul975/Anthropic-Cybersecurity-Skills` · 🟢 Apache-2.0 · ~32.8k ★

754–817 skills mapped to MITRE ATT&CK, NIST CSF 2.0, MITRE ATLAS, D3FEND, NIST AI RMF
and MITRE F3, across 29 domains. **Community-created; not affiliated with Anthropic
despite the name.**

Use as the reference library for the **Agent Security Brain**, given the attack surface
(Browser → Internet → Research → Code execution → Financial data → Portfolio → Broker):
prompt-injection detection · tool permission boundaries · credential isolation · browser
sandboxing · malicious-document detection · untrusted-content isolation · MCP security ·
code-execution sandbox · secrets management · audit logs · transaction approval ·
data-exfiltration prevention.

### 4.7 Documentation tooling — **INTEGRATE (dev-time only)**
`cathrynlavery/diagram-design` · 🟢 MIT · ~39.6k ★ — 29–38 editorial diagram types,
self-contained HTML+SVG, installable as a Claude Code plugin
(`/plugin marketplace add cathrynlavery/diagram-design`). Design system in
`skills/diagram-design/SKILL.md`, runbook in `docs/cookbook.md`.
`joehaddad2000/schematics` ⚪ — alternative, for planning/explaining technical work.

Maintain: `docs/architecture/{system,agent-org,data-flow,quant-pipeline,execution-flow,knowledge-graph,research-loop}.html`.

---

## 5. Indian market data sources

The layer that decides whether any of the above is usable. See
[FEASIBILITY-INDIA.md](FEASIBILITY-INDIA.md) for the full constraint analysis.

| Source | Tier | Gives us | Cost / constraint | Verdict |
|---|---|---|---|---|
| **NSE bhavcopy** | 1 | Daily EOD OHLCV, full history, delisted names | Free; scraping is anti-bot hostile | **INTEGRATE** |
| **BSE bhavcopy** | 1 | Daily EOD, BSE codes | Free | **INTEGRATE** |
| **NSE/BSE announcements** | 1 | Corporate disclosures, results, actions | BSE far easier than NSE | **INTEGRATE** |
| **Company filings / annual reports** | 1 | Fundamentals at source, PIT-capable | Parsing effort | **INTEGRATE** |
| **Kite Connect** | 2 | Live quotes, WebSocket ticks, historical candles, portfolio, orders | Paid; static IP for orders; ~10 orders/sec; personal use only | **INTEGRATE** |
| **NSE real-time / corporate data products** | 1 | Licensed L1/L2/L3, tick-by-tick, fundamentals, shareholding | Commercial agreement; corporate-data subscription quoted ~₹10.6 lakh/yr | **DEFER** |
| **NSE MCP interface** | — | AI-facing market data | Stated educational/informational — **not** for trading or commercial deployment | **REFERENCE** |
| **Screener.in** | 3 | Convenient fundamentals, ratios | Terms unclear; restated (non-PIT) data | **WRAPPED 2026-09-20** (see §4a) |
| **Moneycontrol** | 3 | News (fundamentals are robots-disallowed) | Terms unclear; non-PIT | **WRAPPED 2026-09-20** (see §4a) |
| **AMFI NAV feed** | 1 | Daily NAV + ISIN for ~14k mutual-fund schemes | Free, public, no auth | **INTEGRATED 2026-09-20** |
| **MFCentral** | 1 | A holder's own consolidated MF holdings | **Requires the owner's PAN + OTP** | **OWNER-ONLY** - export the CAS by hand; Claude does not authenticate |
| **RBI** | 1 | Policy, rates, inflation, liquidity | Free | **INTEGRATE** |

### 4a. How the Tier-3 web sources are actually used (2026-09-20)

"Public" is not "licensed", so these wrappers are deliberately narrow and the limits are
in code, not in good intentions:

* **On demand, never crawling.** Moneycontrol fetches happen only for a URL a filing
  already pointed at (`ingest/newsfetch.py`), one at a time, with a crawl delay. Screener
  is fetched per company, on request.
* **robots.txt is obeyed, including wildcards.** Python's `urllib.robotparser` matches
  rule paths by plain prefix, so Moneycontrol's `Disallow: /stocks/company_info/*`
  matched *nothing* and read as allowed. `providers/robots.py` implements `*`, `$` and
  longest-match-wins instead. Consequence: Moneycontrol **fundamentals are off-limits**
  (`/stocks/company_info/`, `/financials/results/` are disallowed) - it is a news source
  here, nothing more. Screener's `/company/<SYMBOL>/` is allowed; `/company/source/
  quarter/*`, `/user/*` and query-sorted listings are not.
* **An allowlist of hosts the owner approved**, so extraction finding an Economic Times
  or Livemint link does not silently become a request to those sites.
* **We store facts about the article, not the article.** Title, publication time and the
  publisher's own `og:description`; never the body.
* **Most news needs no fetching at all.** `events/newsref.py` recovers the headline from
  the filing's own text for 7,146 filings - the exchange quotes it. Fetching is the
  fallback for the ~12.6k filings that carry only a bare link.
* **Tier 3 never outranks Tier 1.** A Screener ratio is cross-checked against the close
  we computed ourselves; disagreement beyond 2% is stored as `quality='disputed'`,
  not averaged away.


| **SEBI** | 1 | Regulation, algo framework, RA/RIA rules | Free; compliance-critical | **REFERENCE** |
| **FII/DII daily flows** | 1 | Regime input | Free | **INTEGRATE** |
| **India VIX** | 1 | Regime input | Free | **INTEGRATE** |
| **CMIE Prowess** | 1 | True point-in-time Indian fundamentals | Institutional pricing | **DEFER (the real fix for PIT)** |
| **YouTube transcripts** | 4 | Largest cheap Indian social/narrative source | Free-ish; noisy | **INTEGRATE first** |
| **Indian finance news RSS** | 3 | Event and narrative signal | Free | **INTEGRATE** |
| **Reddit** | 4 | Thin for Indian equities | Low value | **DEFER** |
| **X / Twitter** | 4 | Real-time narrative | API cost is the binding constraint | **DEFER** |

**Source hierarchy — when sources disagree, Tier 1 wins.** This matters more in India
than the US, because Screener and Moneycontrol diverge from filings more often than US
aggregators diverge from EDGAR.

**Canonical identity: ISIN is primary.** NSE symbols change, BSE codes differ, companies
restructure. ISIN is the only stable join key.

---

## 6. Models

| Model | Type | Access | Role here |
|---|---|---|---|
| **FinBERT-19** (ProsusAI) | Encoder, continual pre-training | HuggingFace | Cheap high-volume sentiment tagging |
| **FinBERT-20** | Encoder, domain pre-training from scratch | HuggingFace | Alternative encoder |
| **FinBERT-21** (IJCAI) | Encoder, mixed-domain | Paper | Reference |
| **FLANG** (SALT-NLP) | Encoder, mixed-domain | HuggingFace | FLUE benchmark baseline |
| **BloombergGPT** | Mixed-domain LLM | Closed | **REJECT** — pre-training from scratch is infeasible for us |
| **FinMA / PIXIU** | Instruction-tuned LLM + FLARE leaderboard | 🟢 MIT | Eval-harness pattern |
| **InvestLM** | Instruction-tuned for investment | GitHub | Reference |
| **FinGPT** | Instruction-tuned, open | 🟢 MIT | **The viable path** — instruction tuning, not pre-training |
| General frontier LLM | Reasoning/orchestration | API | Orchestrator, committee agents, extraction |

**Architecture implication:** instruction-tune a strong general model for reasoning and
orchestration; use small FinBERT-class encoders for narrow, high-volume classification
where an LLM call per document is wasteful. Never pre-train.

**Superseded in part by [ADR-0002](adr/0002-tiered-models.md) (2026-09-19):** models are
chosen by measurement on our own Indian tasks, not by this table. Measured on 165
labelled BSE filing headlines (sentiment for shareholders, macro-F1): FinBERT (ProsusAI,
🟢 Apache-2.0 code) **0.45** - it calls 17 of 19 adverse filings neutral; FinSenti-
Llama-3.2-1B (🟢 Apache-2.0) **0.05** - "positive" for everything; general local models
do far better (llama3.1:8b 0.82, qwen2.5:7b 0.76, phi4 0.72, qwen2.5:3b 0.66). Fin-R1
(SUFE, 7B reasoning; ⚪ licence unlisted on HF) and other thinking models cannot give a
one-token typed decision - reasoning tier only. Live results: `fb models list`.

| Added 2026-09-19 | Licence | Verdict | Use here |
|---|---|---|---|
| [Fin-R1](https://huggingface.co/SUFE-AIFLM-Lab/Fin-R1) (GGUF: bartowski) | ⚪ unlisted | **STUDY** | Reasoning-tier candidate; benchmark before use |
| [FinSenti-Llama-3.2-1B](https://huggingface.co/mradermacher/FinSenti-Llama-3.2-1B-GGUF) | 🟢 Apache-2.0 | **REJECT** (measured) | macro-F1 0.05 on Indian filings |
| [FinBERT](https://github.com/ProsusAI/finBERT) | 🟢 Apache-2.0 | **REJECT for filings** (measured) | Maybe English news later - re-benchmark first |
| Jev (TypeSafe AI, Sept 2026) - typed "System One" decisions | ⚪ cloud, waitlist | **STUDY → pattern adopted** | `llm/system1.py` rebuilds the pattern locally; Jev slots in as a backend once the owner has access |

---

## 7. Datasets and benchmarks

From the [FinLLMs survey](https://github.com/adlnlp/FinLLMs) (⚪ no licence file;
reference only). **Every one of these is English and US/EU-centric — there is no Indian
equivalent in the benchmark set.**

### Six core financial-NLP tasks
| Task | Datasets | Our module |
|---|---|---|
| Sentiment Analysis | Financial PhraseBank (FPB), FiQA-SA, SemEval-2017, StockEmotions | Social & Sentiment layer |
| Text Classification | Headline, FedNLP, **FOMC (Trillion Dollar Words)**, Banking77 | Event Intelligence |
| Named Entity Recognition | FIN, **FiNER-139** (XBRL) | Knowledge-graph entity extraction |
| Question Answering | FiQA-QA, **FinQA**, **ConvFinQA**, **TAT-QA**, PACIFIC | Market Copilot |
| Stock Movement Prediction | StockNet, CIKM18, BigData22 | ⚠ see below |
| Text Summarization | **ECTSum** (earnings calls), MultiLing 2019 | Daily brief, filing digests |

### Eight advanced tasks
| Task | Datasets | Our module |
|---|---|---|
| Relation Extraction | FinRED | **Knowledge-graph edge construction** |
| Event Detection | EDT (news-driven trading) | Event Intelligence |
| Causality Detection | FinCausal20 | Event → sector → company propagation |
| Numerical Reasoning | FiNER-139, FinQA, ConvFinQA, TAT-QA, PACIFIC | Reasoning over statements |
| Structure Recognition | FinTabNet | Table extraction from annual reports |
| Multimodal | MAEC, MONOPOLY | Earnings-call tone — **deferred** (Indian ASR) |
| Machine Translation | MINDS-14, **MultiFin** | **Multilingual Indian documents** |
| Market Forecasting | StockEmotions, EDT, MAEC, MONOPOLY | ⚠ see below |

### ⚠ The finding that shapes the whole system
Stock Movement Prediction shows **weak, contested effect sizes across every benchmark**.
No model — FinLLM, general LLM or task-specific SOTA — demonstrates a reliable edge.

> **The LLM is not the alpha source.** Use it for *reading* — extraction, classification,
> relation-building, summarisation, QA. Leave *prediction* to the quant stack behind the
> Alpha Validation Firewall.

### The India benchmark gap — datasets we must build

Built so far (`tests/fixtures`, Claude-labelled, owner to spot-check): announcement event
type - 168 tuning + 102 held-out; shareholder sentiment - 165 headlines.

1. **RBI policy hawkish/dovish classification** — the Indian FOMC task. Highest value,
   most tractable. Build first.
2. Labelled NSE/BSE announcement corpus — event type, materiality, affected entities.
3. Indian earnings-call transcripts — summarisation and tone.
4. Indian regional-language financial text — essentially unbenchmarked worldwide; the
   strongest research-novelty angle.
5. Entity-resolution / corporate-action gold set for our own security master.

---

## 8. Product benchmarks

Studied for concepts, not integration.

| Product | What it does exceptionally well | What we take |
|---|---|---|
| **Bloomberg Terminal** | Market data + news + research + analytics + collaboration + execution; agentic AI added; Bloomberg India hub launched Aug 2026 | Terminal ambition — but compete on *interpretation*, not data volume |
| **Finviz** | Visual market discovery; **Finviz Matrix** organises thousands of stocks by industry × market cap | **India Market Matrix** — plus click a sector → AI explains why it moved |
| **InvosWealth** | QuantAI screening 4,000+ instruments on fundamentals/value/momentum/timing with composite score; momentum radar; thematic baskets. Research, not a trading platform | Indian quant/signal benchmark — but go deeper than `Stock → Score` |
| **Messari** | Asset intelligence + narratives + events + AI; **Signals** decomposed as Mindshare → Sentiment → Why; Copilot with citations | **WHAT / HOW / WHY** per stock; Market Copilot with mandatory sourcing |

Positioning: Finviz *sees* the market · Invos *quantifies* it · Messari *understands* it ·
TradingAgents *debates* it · Fincept *operates* the terminal · **Financial-Brain
continuously investigates, remembers, reasons, tests and learns.**

The layer none of them provides: the **overnight situational-awareness brief** —
"12 important things changed overnight."

---

## 9. Research and reference material

| Resource | Type | Use |
|---|---|---|
| [A Survey of LLMs in Finance](https://arxiv.org/abs/2402.02315) | Survey (Neural Computing & Applications 2025) | Task taxonomy, evaluation map, technique ladder |
| **AgentQuant** | Paper | Research → construction → backtesting → walk-forward/Monte Carlo → trade diagnosis |
| **RD-Agent** (Microsoft) | Framework | Automated factor mining; ships with Qlib |
| Agentic-quant literature | Papers | factor mining → signal discovery → portfolio construction → execution → risk management, with memory and feedback loops |
| *Advances in Financial Machine Learning* | Book | The methodology MLFinLab implements — use the book, not the code |

**Venues to track:** FNP (Financial Narrative Processing) · FinNLP · ECONLP ·
AAAI AI-for-Financial-Services bridge · MUFFIN · KDF.

---

## 9a. Document ingestion and orchestration

### Docling — **INTEGRATE** (Phase 1–2)
`docling-project/docling` · MIT

The gap the original register left open. Annual reports, results PDFs and investor
presentations are core inputs, and the register named **Browser Use** for them — which is
an acquisition tool, not a parser. Docling handles PDFs, tables, XBRL, Office documents
and OCR with structured export.

Correct division of labour:
- **Browser Use** — *find and fetch* the document (last-mile, sandboxed)
- **Docling** — *parse* it into structure
- **C00 raw lake** — keep the original bytes either way

Feeds [C11 Document intelligence](BUILD-FLOW.md) and the Structure Recognition /
Numerical Reasoning tasks in §7.

### Dagster — **DEFER**
Evaluate for scheduled data assets and lineage, but **only once simple idempotent jobs
and a scheduler are no longer enough**. Phase 0 already provides idempotence,
replayability, lineage, quarantine and run history in ~300 lines (`ingest/job.py`);
adding an orchestration framework now would be tooling ahead of need.

Migrate when: multiple interdependent asset graphs, backfill coordination across sources,
or partition-aware retries become the daily problem.

---

## 9b. Efficient use of the register

The register is directionally right; the practical core should be smaller.

| Group | Use efficiently | Do not do |
|---|---|---|
| **Build ourselves** | Security master, PIT store, Indian cost model, evidence ledger, world state, decision database, outcome attribution, India implementability gate | Delegate these to a generic repo |
| **Integrate early** | TA-Lib (deterministic indicators), Docling (filings), one portfolio library later | Add agent frameworks before the data exists |
| **Evaluate, then pick one** | Qlib **or** Vibe-Trading; skfolio **or** Riskfolio-Lib | Run overlapping engines in production |
| **Wrap behind interfaces** | NautilusTrader, Browser Use, OpenBB, OpenViking | Let their data/models/licences become lock-in |
| **Study only** | Fincept, Paperclip, OntoBricks, EntroPy, FinRL, FinRL-Meta, FinGPT, MLFinLab methods | Make them runtime dependencies now |
| **Reject / defer** | Hummingbot, cloudQuant/backtrader, Bloomberg-scale UI, autonomous trading, X firehose | Build anything with no India-specific payoff |

### Revised per-repo decisions

- **TA-Lib** — integrate in Phase 2. A deterministic feature service, not an AI dependency.
- **Docling** — the missing Phase-1/2 document-ingestion resource. Added above.
- **TradingAgents** — reuse the committee pattern and structured-output ideas; do **not**
  import its US-centric tools, data paths or workflow unchanged.
- **Vibe-Trading** — contained proof-of-concept, **not a core dependency yet**. Its India
  support uses Yahoo/yfinance-style NSE/BSE symbols: fine for research prototypes,
  inadequate as the canonical, licensed, point-in-time spine — which Phase 0 now provides
  properly. Downgraded from INTEGRATE to *evaluate-then-choose*.
- **Qlib** — Phase 3, for factor research and experiment structure. Choose it if we want
  maximum control; otherwise validate Vibe-Trading first. **Not both as primary.**
- **NautilusTrader** — best candidate for event-driven backtesting and paper execution,
  behind our own interface. LGPL is workable, but **there is no official Kite adapter** —
  we build and test that ourselves. Budget for it.
- **skfolio vs Riskfolio-Lib** — pick one primary after a small benchmark against our
  actual constraints; PyPortfolioOpt stays a cross-check, not a parallel core.
- **OpenBB** — **not the data plane.** AGPL-3.0, and it does not solve Indian official
  feeds, filings or PIT data. Optional provider adapter behind `providers/base.py` only.
- **OpenViking** — **skip for the MVP.** PostgreSQL/pgvector plus object storage and the
  decision database are enough. Revisit only if hierarchical retrieval becomes a
  *measured* bottleneck. This supersedes open decision #4: the answer is "neither, yet".
- **Fincept** — study terminal/workflow ergonomics; do not fork for any commercial path.
- **Paperclip** — defer until multiple agents genuinely need budgets, delegation and
  long-running task ownership.
- **FinRL / FinRL-Meta** — research only, never on a live or paper-trading control path.
- **MLFinLab / EntroPy** — take methodology (deflated Sharpe, multiple-testing controls,
  leakage checks), not code, unless licensing and maturity are verified.

> Repositories should accelerate commodity capabilities. They must not define the product.

What Financial-Brain must own: India-specific point-in-time data · ISIN-based identity and
corporate-action history · promoter/group/pledging intelligence · evidence → decision →
outcome memory · India-valid strategy and cost validation. That is where the defensible
advantage accumulates — and Phase 0 has started the first two.

---

## 10. Licensing ledger

| Resource | Licence | Commercial implication |
|---|---|---|
| TradingAgents | Apache-2.0 | Safe |
| Vibe-Trading | MIT | Safe |
| Qlib | MIT | Safe |
| FinRL / FinRL-Meta / FinGPT | MIT | Safe |
| PyPortfolioOpt / OptimalPortfolios / ml-quant-trading / EntroPy | MIT | Safe |
| skfolio / Riskfolio-Lib | BSD-3 | Safe |
| TA-Lib (python) | BSD-2 | Safe |
| Browser Use / Diagram Design / Scientific Agent Skills / PIXIU | MIT | Safe (review skills before install) |
| Anthropic Cybersecurity Skills / Agent Memory / Hummingbot / Zipline | Apache-2.0 | Safe |
| NautilusTrader | LGPL-3.0 | Link, don't modify; keep behind our interface |
| **OpenBB** | **AGPL-3.0** | ⚠ SaaS triggers §13 — **WRAP** |
| **OpenViking** | **AGPL-3.0** | ⚠ Replaceable interface only |
| **Fincept Terminal** | **AGPL-3.0-or-later** | ⚠ Do not fork commercially; Enterprise is proprietary |
| **cloudQuant/backtrader** | **GPL-3.0** | ⚠ Avoid in favour of Nautilus |
| **MLFinLab** | **Proprietary** | ⚠ Methods only; do not vendor |
| FinLLMs / schematics / quant-factor-research / Fundamental-Factor-Mining | **No licence** | ⚪ All rights reserved — read, don't copy |
| Paperclip / OntoBricks / awesome-harness-engineering / Kronos / quant-research-lab | Unverified | Confirm before use |
| **NSE market & corporate data** | Commercial agreement | Paid; redistribution restricted; MCP offering educational only |
| **Screener / Moneycontrol** | Terms unclear | Public ≠ licensed |
| **SEBI retail algo framework** | Regulation | Constrains the execution layer |
| **SEBI RA/RIA regulations** | Regulation | Triggered the moment recommendations reach anyone but you |

---

## 11. Open decisions

| # | Decision | Blocks | Status |
|---|---|---|---|
| 1 | Personal tool or commercial product? | AGPL exposure, data budget, SEBI RA/RIA critical path | **Decided 2026-09-19: personal use now; a product only if it works out.** AGPL tools usable, but kept behind our own interfaces so a later product switch is cheap; no redistribution of exchange data; no recommendations to others |
| 2 | Start the PIT snapshot store this month? | The only dataset that compounds and cannot be bought back | **Open — every month costs data** |
| 3 | One execution engine: Nautilus vs backtrader vs own | Execution layer design | Recommend **Nautilus** |
| 4 | Does OpenViking replace part of our memory, or add a third store? | Memory architecture | **Open** |
| 5 | Paperclip now or later? | Agent orchestration | Recommend **later** — §17 says no large hierarchy initially |
| 6 | Primary portfolio library: skfolio or Riskfolio-Lib? | Portfolio Brain | Open, low stakes |
| 7 | Verify Paperclip / OntoBricks / harness-engineering licences | Any integration | **Open** |
| 8 | Vibe-Trading's claimed NSE/BSE support — real or aspirational? | Quant Brain effort estimate | **Open — verify** |

---

## 12. Discipline

Twenty-plus repositories is already past the point where adding more makes the
architecture worse rather than better. Three rules:

1. **Nothing becomes a dependency without a verdict row in §1.**
2. **Every INTEGRATE must name what it replaces.** If it replaces nothing, it is STUDY.
3. **Licence before code.** Two load-bearing assumptions were already wrong; assume
   others are.

The edge is not the agent architecture (replicable) or the model (rented). It is the
point-in-time dataset accumulated from day one, the Indian promoter/group/pledging
knowledge graph, the decision-outcome-experience memory, and multilingual Indian
document understanding.
