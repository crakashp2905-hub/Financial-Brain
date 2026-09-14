# Financial-Brain — Build Flow

What must be built, in what order, and what each piece needs to exist.

Companion documents: [ARCHITECTURE.md](ARCHITECTURE.md) (why) ·
[RESOURCES.md](RESOURCES.md) (what we use) ·
[FEASIBILITY-INDIA.md](FEASIBILITY-INDIA.md) (what constrains us).
The same material is navigable as a graph in the [Obsidian vault](../brain/00-Meta/Home.md).

---

## 1. The flow at a glance

```
                        ┌─────────────────────────────┐
                        │   PHASE 0 · FOUNDATIONS     │
                        │   nothing works without it  │
                        └──────────────┬──────────────┘
      C01 Security master ─────────────┤
      C02 Provider abstraction ────────┤
      C03 PIT fundamentals store ──────┤   ← start this month
      C04 Universe snapshots ──────────┤
      C05 Indian cost model ───────────┘
                                       │
                        ┌──────────────▼──────────────┐
                        │   PHASE 1 · PERCEPTION      │
                        │   first useful output       │
                        └──────────────┬──────────────┘
      C06 Event intelligence ──────────┤
      C07 Market Regime Brain ─────────┤
      C08 World state ─────────────────┤
      C09 Evidence ledger ─────────────┤
      C10 Daily brief ─────────────────┘   ← the MVP's visible surface
                                       │
                        ┌──────────────▼──────────────┐
                        │ PHASE 2 · RESEARCH & COMMITTEE│
                        └──────────────┬──────────────┘
      C11 Document intelligence ───────┤
      C12 Feature service ─────────────┤
      C13 Financial skill library ─────┤
      C14 Investment Constitution ─────┤
      C15 Social intelligence ─────────┤
      C16 Investment Committee ────────┤
      C17 Decision record & lifecycle ─┤
      C20 Agent Security Brain ────────┤
      C21 India evaluation benchmark ──┤
      C24 Knowledge Graph ─────────────┘
                                       │
                        ┌──────────────▼──────────────┐
                        │   PHASE 3 · QUANT, GATED    │
                        └──────────────┬──────────────┘
      C19 India Implementability Gate ─┤   ← reject before backtesting
      C22 Alpha Validation Firewall ───┤   ← reject before paper trading
      C23 Strategy registry & paper ───┘
                                       │
                        ┌──────────────▼──────────────┐
                        │   PHASE 4 · EXECUTION       │
                        └──────────────┬──────────────┘
      C18 Execution gateway ───────────┘   ← human approval, always
                                       │
                        ┌──────────────▼──────────────┐
                        │   PHASE 5 · LEARNING        │
                        │   this is the actual IP     │
                        └─────────────────────────────┘
      C25 Outcome attribution & calibration
                                       │
                                       └──────► back to C08 World state
```

The loop only closes at Phase 5. Everything before it is machinery for producing
decisions worth learning from.

---

## 2. Phase 0 — Foundations

**Goal:** a trustworthy Indian market dataset. No AI in this phase at all.
**Exit test:** reproduce any day's investable universe, adjusted prices and
known-at-the-time fundamentals for any past date, from cold storage.

| # | Component | Build | Resources needed |
|---|---|---|---|
| **C01** | Security master | ISIN-keyed identity, symbol history, corporate actions, adjusted/unadjusted OHLCV | NSE bhavcopy · BSE bhavcopy · company filings · PostgreSQL + TimescaleDB |
| **C02** | Data provider abstraction | `financial_brain.data.Provider` interface | OpenBB *(as one implementation, behind the interface — AGPL)* |
| **C03** | PIT fundamentals store | `observed_at` on every fact, never overwrite; key to filing timestamps | NSE/BSE announcements · company filings · Screener *(non-PIT, flagged)* |
| **C04** | Universe snapshots | Daily investable-universe snapshot: listings, delistings, suspensions, T2T, ASM/GSM | NSE/BSE bhavcopy |
| **C05** | Indian cost model | STT both legs, stamp duty, exchange + SEBI fees, GST, brokerage, impact by liquidity bucket, circuit/ASM/T2T flags | NSE circulars, broker schedules |

**Why it is first:** [point-in-time fundamentals](FEASIBILITY-INDIA.md#21-point-in-time-fundamentals--the-killer)
and [survivorship](FEASIBILITY-INDIA.md#22-survivorship-and-universe-reconstruction) are
the two blockers that cannot be fixed retroactively. Every month C03 waits is a month of
data that cannot be bought back later.

**Deliberately unglamorous.** Expect several weeks and no visible product.

---

## 3. Phase 1 — Perception

**Goal:** the system knows what is happening and can say why it matters.
**Exit test:** a cited daily brief you would actually read before the market opens.

| # | Component | Build | Resources needed |
|---|---|---|---|
| **C06** | Event intelligence | Company / macro / market event extraction and classification | NSE+BSE announcements · news RSS · RBI |
| **C07** | Market Regime Brain | Regime classification from indices, VIX, flows, macro, breadth, rotation | FII/DII flows · India VIX · bhavcopy · RBI |
| **C08** | World state | Versioned immutable snapshot every decision references | — (built on C06, C07) |
| **C09** | Evidence ledger | Provenance chain: source → hash → times → confidence → decisions used | — |
| **C10** | Daily brief | "What changed since yesterday?" across market, sectors, portfolio | Kite Connect *(read-only)* · LLM API |

**First contact with an LLM** is here, and only for reading and summarising — never for
prediction. See [Stock movement prediction has no edge](ARCHITECTURE.md).

---

## 4. Phase 2 — Research and committee

**Goal:** structured theses with evidence, uncertainty and invalidation conditions.
**Exit test:** a company thesis card whose every claim opens its source, and whose
invalidation conditions are actively monitored.

| # | Component | Build | Resources needed |
|---|---|---|---|
| **C11** | Document intelligence | Filings/reports → concepts, rules, claims, evidence | Company filings · Browser Use *(sandboxed)* · LLM API |
| **C12** | Feature service | Deterministic indicators and factors, versioned | TA-Lib |
| **C13** | Financial skill library | 16 procedural quant skills, not one giant agent | Scientific Agent Skills *(~12 adapted)* · Vibe-Trading skills |
| **C14** | Investment Constitution | The investor's own rules, retrievable by agents | Your PDFs, notes, trade history |
| **C15** | Social intelligence | Narrative + anomaly detection; wakes research, never trades | YouTube transcripts *(first)* · news RSS |
| **C16** | Investment Committee | 6 agents + orchestrator, bull/bear debate, disagreement measurement | TradingAgents · LLM API |
| **C17** | Decision record & lifecycle | Immutable decision contract + state machine | — |
| **C20** | Agent Security Brain | Injection detection, tool allowlists, credential vaulting, audit logs, sandboxing | Anthropic Cybersecurity Skills *(reference)* |
| **C21** | India evaluation benchmark | Labelled Indian datasets — RBI hawkish/dovish first | RBI · PIXIU/FLARE *(harness pattern)* |
| **C24** | Knowledge Graph | Company/sector/policy relations; promoter groups, pledging, contagion | Company filings · OntoBricks *(modelling approach)* |

**C20 and C21 are not optional extras.** Security is load-bearing the moment C11 reads
untrusted documents; evaluation is load-bearing before any autonomy is granted.

---

## 5. Phase 3 — Quant, gated

**Goal:** strategies that survive honest validation.
**Exit test:** a few hundred hypotheses rejected, and survivors carrying Deflated Sharpe,
walk-forward and capacity evidence.

| # | Component | Build | Resources needed |
|---|---|---|---|
| **C19** | India Implementability Gate | Reject untradeable hypotheses *before* any backtest | C05 cost model |
| **C22** | Alpha Validation Firewall | IC/ICIR, multiple testing, redundancy, Deflated Sharpe, walk-forward, OOS, costs, capacity, regime stability | EntroPy *(blueprint)* · Qlib · MLFinLab *(methods only)* |
| **C23** | Strategy registry + paper trading | Versioned strategies with full lineage, paper execution | Vibe-Trading · NautilusTrader |

Order matters and is unusual: **implementability before backtest, validation before
paper.** Most systems backtest first and discover untradeability last.

Price/technical factors first — they *are* validatable on Indian data. Fundamental factor
mining waits for C03 to accumulate real point-in-time history.

---

## 6. Phase 4 — Execution

**Goal:** proposals reach the broker only through a human.
**Entry condition:** 6+ months of paper results. Not sooner.

| # | Component | Build | Resources needed |
|---|---|---|---|
| **C18** | Execution gateway | Risk → exposure → position → market-state → compliance → human approval → broker | Kite Connect *(paid, static IP)* · NautilusTrader |

**Verify SEBI's current retail-algo framework and your broker's requirements before
writing the order path.** The harness enforces validation, not the model.

---

## 7. Phase 5 — Learning

**Goal:** the loop closes and the system knows where its edge is.

| # | Component | Build | Resources needed |
|---|---|---|---|
| **C25** | Outcome attribution & calibration | Attribute each closed decision to beta, sector, factor, surprise, valuation, timing, cost, thesis error, risk-rule error or luck. Track accuracy by horizon, regime, sector, cap band, strategy | — (built on C17, C23) |

Adaptation only via: outcome → attribution → hypothesis → versioned experiment →
validation → paper → risk committee → human-approved promotion. Rollback supported.
Never a silent prompt, model, score or rule change.

**This is the actual IP.** It cannot be built early because it has nothing to learn from
until Phases 2–4 have produced recorded decisions with measured outcomes.

---

## 8. Resources needed, by kind

### 8.1 Software to run (INTEGRATE — see [RESOURCES.md](RESOURCES.md))
Vibe-Trading · TradingAgents · Qlib · skfolio *or* Riskfolio-Lib · PyPortfolioOpt ·
TA-Lib · Diagram Design *(dev-time)*

### 8.2 Software to wrap behind our own interface
OpenBB 🔴 AGPL · OpenViking 🔴 AGPL · NautilusTrader 🟡 LGPL · Browser Use

### 8.3 Infrastructure
| Need | For |
|---|---|
| PostgreSQL + TimescaleDB | prices, fundamentals, events, decisions, outcomes |
| Vector store (pgvector or Qdrant) | research corpus retrieval |
| Graph store | knowledge graph — start in Postgres, migrate only if it hurts |
| Redis | caching, job queues |
| Object storage | raw filings, transcripts, snapshots (immutability matters) |
| Python 3.11+, FastAPI, Pydantic | services |
| Scheduler | daily ingestion, regime computation, brief generation |
| Backups | the PIT store is irreplaceable — treat accordingly |

### 8.4 Accounts and subscriptions
| Resource | Phase | Cost | Note |
|---|---|---|---|
| Kite Connect | 1 (read) / 4 (orders) | paid monthly | static IP needed for orders |
| LLM API | 1 onward | usage-based | orchestration + extraction |
| Static IP | 4 | small | Kite order requirement |
| NSE licensed data | deferred | ~₹10.6 lakh/yr corporate data | only if commercial |
| CMIE Prowess | deferred | institutional | the real PIT fix |
| X API | deferred | high | the reason social is deprioritised |

### 8.5 Your own inputs — nothing substitutes for these
- Investment philosophy, rules, exclusions → **C14 Investment Constitution**
- Chart-reading material and annotated examples → C14
- Past trades with reasoning, including the bad ones → C25 calibration baseline
- Books, papers, notes → C11 research corpus

### 8.6 Skills the build assumes
Python data engineering (the largest share of Phase 0) · time-series and market
microstructure · statistical validation (multiple testing, Deflated Sharpe, walk-forward) ·
LLM orchestration and tool design · Indian regulatory literacy (SEBI, exchange data rights)

### 8.7 Decisions that gate resources
| Decision | Gates |
|---|---|
| Personal tool or product? | AGPL exposure, data budget, SEBI RA/RIA on critical path |
| Start PIT store this month? | the only compounding asset |
| One execution engine | Nautilus vs backtrader vs own |
| OpenViking: replace or add? | memory architecture |
| Paperclip now or later? | agent orchestration scope |

---

## 9. Critical path

```
C01 Security master
  └─► C04 Universe snapshots ─► C05 Indian cost model ─► C19 Implementability Gate
  └─► C03 PIT fundamentals store ──────────────────────► C22 Alpha Validation
  └─► C06 Event intelligence ─► C07 Regime Brain ─► C08 World state
                                                      └─► C09 Evidence ledger
                                                            └─► C10 Daily brief
                                                            └─► C17 Decision record
                                                                  └─► C18 Execution
                                                                        └─► C25 Learning
```

**C01 blocks everything.** C03 blocks nothing immediately but blocks the most valuable
work permanently if it starts late — it is the one component whose cost rises with delay.

---

## 10. What is deliberately *not* being built

| Not building | Why |
|---|---|
| Twenty integrated repositories | Frankenstein risk; integrate a small core |
| Autonomous live trading | Regulatory and prudential; indefinitely deferred |
| RL controlling anything | Research firewall only |
| A Bloomberg-scale terminal | Not a first project; compete on interpretation |
| A large agent hierarchy | Six agents plus orchestrator, not twenty |
| Pre-trained financial LLM | Infeasible cost; instruction-tune instead |
| 1,000+ fundamental factors | Blocked until C03 accumulates PIT history |
| Long-short cash-equity strategies | Shorting unavailable in India |
| Multimodal earnings-call audio | Indian ASR quality is its own project |
| X/Twitter firehose | Cost; YouTube first |
| A multi-user terminal product | Gated on SEBI RA/RIA and data licensing |

---

## 11. Milestones worth stopping at

| Milestone | You have | Decide |
|---|---|---|
| **M0** End of Phase 0 | A trustworthy Indian dataset nobody else has started accumulating | Continue solo, or is this already the asset? |
| **M1** C10 shipped | A daily brief you rely on | Is the brief alone worth the project? |
| **M2** Phase 2 complete | Thesis cards with monitored invalidation | Personal tool or product? (forces D1) |
| **M3** First strategy past C22 | Honest validation evidence | Paper trade it, or keep researching? |
| **M4** 6 months paper results | Evidence the decisions are calibrated | Execution, or stop at research? |

Each milestone is independently useful. That is the point — the system stays valuable
even if no tradeable alpha is ever found.
