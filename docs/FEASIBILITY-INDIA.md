# Feasibility of Financial-Brain in Indian Markets

Assessment of everything in [SOURCE-CONVERSATION-SPEC.md](SOURCE-CONVERSATION-SPEC.md)
against the realities of NSE/BSE, Indian data availability, SEBI regulation and the
economics of a solo/small-team build.

**Verdict in one line:** the *architecture* is sound and largely buildable; the
*research ambitions layered on top of it* are constrained less by AI and almost entirely
by *Indian data quality, market microstructure and regulation*. Roughly **70% of the
spec is feasible**, ~20% is feasible only in a degraded form, and ~10% should be cut or
deferred indefinitely.

> Regulatory specifics below (SEBI algo thresholds, NSE data pricing, RA/RIA triggers)
> must be **verified against the current circulars before you act on them** — they have
> been revised repeatedly and this document is not legal advice.

---

## 1. Scorecard

| Layer | Feasible in India? | Main constraint |
|---|---|---|
| Terminal UI / workstation | 🟢 High | Effort only |
| Data plane — EOD prices, corporate actions | 🟢 High | Bhavcopy + Kite; adjustment work |
| Data plane — real-time tick/depth | 🟡 Medium | Kite WebSocket OK personally; licensed feed needed commercially |
| Data plane — fundamentals (current) | 🟢 High | Screener/MC scrape or filings parse |
| Data plane — **point-in-time fundamentals** | 🔴 Low | **No PIT source exists; this is the single biggest blocker** |
| Data plane — delisted/survivorship history | 🔴 Low | Not published cleanly; must be reconstructed |
| Data plane — index constituent history | 🟠 Low-Med | NIFTY membership history not freely available |
| Knowledge graph (companies, promoters, suppliers) | 🟡 Medium | Filings give some; supplier/customer links mostly absent |
| Event Intelligence (announcements, results, actions) | 🟢 High | BSE easy; NSE anti-bot hostile |
| Market Regime Brain | 🟢 High | All inputs freely available |
| Social intelligence (X / Reddit / YouTube) | 🟠 Low-Med | **X API cost is the binding constraint**, not the modelling |
| Multi-agent investment committee | 🟢 High | LLM cost is the only real limit |
| Research Brain (papers → hypotheses) | 🟢 High | Works; quality gated by §2 data |
| Quant Brain — technical/price factors | 🟢 High | Prices are clean enough |
| Quant Brain — **fundamental factor mining (1,000+)** | 🔴 Low | PIT + universe size + multiple testing |
| Alpha Validation Firewall | 🟢 High | Pure statistics; *more* necessary in India, not less |
| Long-short factor portfolios | 🔴 Low | **Cannot short cash equities**; F&O universe ~200 names |
| Portfolio / Risk Brain | 🟢 High | skfolio/Riskfolio/PyPortfolioOpt work fine |
| Paper trading | 🟢 High | — |
| Live execution via Kite | 🟡 Medium | SEBI algo registration + static IP + broker approval |
| Autonomous live trading | 🔴 Low | Regulatory + prudential; keep deferred as the spec already says |
| Commercialisation (advice to others) | 🔴 Low | **SEBI RA/RIA registration required** |
| Commercialisation (data redistribution) | 🔴 Low | Exchange licensing |

---

## 2. The five real blockers

### 2.1 Point-in-time fundamentals — the killer
Screener, Moneycontrol and most aggregators serve **restated, as-of-today** financials.
They do not tell you what the market *knew* on 14 Aug 2019. Every fundamental backtest
built on them silently contains look-ahead bias:

- restatements are folded back into history
- the *reporting date* (when the number became public) is usually absent; only the
  *period end* is given — in India the gap is routinely 30–60 days
- ratios are recomputed with today's share count after splits/bonuses

**Consequence:** §8 "Fundamental Factor Factory" and repo #19 (1,000+ fundamental
factors) cannot be validated on Indian history the way they were on China A-shares. Any
Sharpe you produce there is fiction.

**Mitigations, in order of value:**
1. **Start your own PIT store today.** Snapshot every fundamental you ingest with an
   `observed_at` timestamp and never overwrite. In three years you own a dataset nobody
   else has. This is cheap and should be in V1, not V3.
2. Parse **filing dates** from BSE/NSE announcements and key every fundamental to the
   announcement timestamp, not the quarter end. Recovers partial PIT for recent years.
3. Buy PIT data (Refinitiv/S&P/CMIE Prowess) if this ever becomes commercial. CMIE
   Prowess is the realistic Indian option and is priced for institutions.
4. Until then: **restrict fundamental factors to slow-moving, restatement-resistant
   quantities** (market cap, sector, price-derived ratios using trailing reported EPS
   with a deliberate 90-day lag) and treat everything else as unvalidatable.

### 2.2 Survivorship and universe reconstruction
NSE/BSE bhavcopy archives exist per day and *do* contain delisted names historically,
but you must reconstruct the point-in-time universe yourself: delistings, suspensions,
symbol changes, mergers, T2T moves, ASM/GSM inclusion. There is no clean published
"NIFTY 500 membership as of date X" series.

Without this, momentum and quality backtests in India overstate returns materially —
the failures are exactly the names that vanish from a current-universe dataset.

**Mitigation:** build a `security_master` with symbol history, ISIN as the join key
(ISIN survives symbol changes), and a daily universe snapshot from bhavcopy. Budget
several weeks for this. It is unglamorous and it is the foundation of everything in §8
and §15.

### 2.3 Microstructure and cost — where Indian backtests go to die
The spec's cost modelling requirement is not optional here; India is unusually punitive:

- **STT** on delivery equity is charged on both legs; plus stamp duty, exchange txn
  charges, SEBI turnover fee, GST, brokerage. Round-trip friction on delivery trades is
  a large multiple of a US equivalent.
- **Impact cost** outside the top ~500 names is severe. A ₹10 lakh order in a smallcap
  can move it.
- **Circuit limits** (2/5/10/20%) mean you frequently *cannot* trade at the signal price
  — the backtest fills, reality doesn't.
- **ASM / GSM surveillance**, T2T segment, periodic call auctions — names get frozen or
  forced to delivery-only with little notice.
- **No shorting in cash beyond intraday.** SLB is thin. Long-short academic factor
  results (which is most of the literature the Research Brain will ingest) are **not
  implementable** except via futures, and F&O is restricted to roughly 200 names with
  periodic entry/exit from the list.

**Consequence:** the Research Brain will happily read a US long-short momentum paper and
generate a hypothesis that is untradeable in India. The **Hypothesis Engine needs an
India-implementability gate** — a component the original spec does not have. Add it:

```
Hypothesis -> [ INDIA IMPLEMENTABILITY GATE ]
                - long-only feasible?
                - universe liquid enough at target AUM?
                - turnover survives STT + impact?
                - F&O available if shorting required?
                - circuit / ASM / T2T exposure?
              -> PASS -> Factor Builder
              -> FAIL -> record why, do not backtest
```

### 2.4 Regulation
Three distinct gates, often conflated:

1. **Algorithmic order placement.** SEBI's retail-algo framework routes algo orders
   through the broker with registration/tagging, and treats order flow above a
   per-second threshold as algorithmic. Kite additionally requires a static IP for order
   APIs. Personal, low-frequency, human-approved order placement is the easy path; a
   continuously trading agent is the regulated one. **Verify the current circular before
   building the execution layer.**
2. **Investment advice.** The moment recommendations go to anyone other than you,
   SEBI's Research Analyst / Investment Adviser regulations apply. This is the hard gate
   on the Bloomberg-terminal-product ambition in §5–7 — it is a licensing question, not
   an engineering one, and it should be settled before any commercial design work.
3. **Data redistribution.** Showing licensed exchange data to third parties requires an
   agreement with the exchange. Personal use of your own Kite feed is fine; a
   multi-user terminal is not.

**Net:** personal-use Financial-Brain is unblocked. Product Financial-Brain has a
regulatory precondition that costs money and time and should be priced in from day one.

### 2.5 Social data economics
The §2 social layer is the most-specified and least-feasible-as-written part of the
spec. X API pricing for anything beyond trivial volume is the binding constraint; Reddit
is thin for Indian equities; YouTube transcripts are the only genuinely cheap, rich
source and Indian finance YouTube is large.

**Mitigation:** invert the priority — **YouTube transcripts first**, Indian finance news
RSS second, Telegram/forums third, X last and only if funded. Keep the 5% decision
weight the spec assigns it; do not spend 30% of the build there.

---

## 3. What the spec gets *right* for India

- **Tier-1-wins source hierarchy** matters more in India than the US, because Screener
  and Moneycontrol diverge from filings more often than US aggregators diverge from EDGAR.
- **Canonical security identity** was correctly named as a hard problem. In India it is
  worse than the TCS/TCS.NS/532540 example — ISIN is the only stable key across NSE
  symbol changes, BSE codes, and corporate restructurings. Make ISIN primary.
- **Knowledge graph** has a genuinely India-specific payoff the US market doesn't offer:
  **promoter group structures, pledging, related-party transactions, group contagion**
  (one group's stress propagating across listed entities). That is real, underexploited
  edge and it is graph-shaped.
- **Regime brain** is well-suited: FII/DII daily flows are published free, India VIX is
  free, breadth is computable from bhavcopy. This layer is cheap and high-value.
- **Alpha Validation Firewall (§15)** is *more* essential in India, because the smaller
  universe (~2,000 names, ~500 liquid) and shorter clean history make multiple-testing
  false positives far likelier than on US data. Deflated Sharpe is not optional.
- **Read-only Kite first, human approval always** matches the regulatory reality exactly.
- **LLM is not the alpha source** is corroborated by the FinLLMs survey (§16): stock
  movement prediction from text has no reliable edge in any published benchmark.

---

## 4. What to cut or defer

| Item | Call | Reason |
|---|---|---|
| 1,000+ fundamental factor mining | **Defer to V4+** | No PIT data; will produce false positives |
| Long-short factor strategies | **Cut for cash equities** | Shorting unavailable; revisit via F&O only |
| RL / FinRL controlling anything | **Research only** | Spec already says this; hold the line |
| BloombergGPT-style pre-training | **Cut** | Infeasible cost; instruction-tune instead (§16) |
| Multimodal earnings-call audio | **Defer** | ASR quality on Indian calls is a project of its own |
| X / Twitter firehose | **Defer** | Cost; YouTube first |
| Fincept fork | **Study only** | AGPL-3.0 vs any commercial intent |
| OpenViking as core memory | **Wrap behind an interface** | AGPL-3.0; spec already says this |
| Autonomous live trading | **Indefinite defer** | Regulatory + prudential |
| Multi-user terminal product | **Gate on SEBI RA/RIA + data licensing** | Not an engineering decision |

---

## 5. Recommended India-adjusted build order

**Phase 0 — Foundations (weeks 1–8).** The unglamorous work that everything depends on.
- `security_master` keyed on ISIN, with symbol/exchange history
- daily bhavcopy ingestion (NSE + BSE), full history, into TimescaleDB
- corporate-action adjustment pipeline (splits, bonus, rights, mergers)
- **PIT snapshot store — start recording `observed_at` on every fundamental now**
- realistic Indian cost model (STT both legs, stamp duty, exchange, SEBI, GST,
  brokerage, impact by liquidity bucket, circuit/ASM flags)
- point-in-time universe snapshots

**Phase 1 — Perception (weeks 9–16).**
- Kite read-only: quotes, OHLCV, portfolio
- BSE/NSE announcements ingestion + Event Intelligence
- Market Regime Brain (FII/DII, VIX, breadth, sector rotation)
- "What changed since yesterday" brief — first genuinely useful daily output

**Phase 2 — Research & committee (weeks 17–28).**
- Document Intelligence over filings and annual reports
- Investment Constitution from your own PDFs/notes
- Multi-agent committee (6 agents + orchestrator, per §1.18)
- Thesis memory with explicit invalidation conditions, watched continuously
- Every claim sourced and timestamped

**Phase 3 — Quant, gated (weeks 29–44).**
- Technical/price factors first (these *are* validatable on Indian data)
- **India Implementability Gate** before any backtest
- Alpha Validation Firewall (IC/ICIR, multiple testing, Deflated Sharpe, walk-forward,
  OOS, capacity, regime stability)
- Strategy Judge → registry → paper trading

**Phase 4 — Execution (only after 6+ months of paper results).**
- Trade proposals → risk engine → human approval → Kite
- Verify SEBI algo requirements with your broker before writing the order path

**Phase 5 — Learning loop.**
- Outcome memory, per-agent track records, dynamic weighting, adaptation engine
- This is the actual IP (§11) and it only becomes real once Phases 2–4 have produced
  enough recorded decisions to learn from

---

## 6. Honest assessment of the ambition

**As a personal system:** entirely feasible, and unusually well-specified. A disciplined
solo build reaches Phase 2 in a few months and Phase 3 within a year. The regime brain,
event intelligence, thesis memory and daily brief are useful long before any strategy is
validated — which is the right shape for a project like this, because it stays valuable
even if no alpha is ever found.

**As a differentiated product:** the defensible edge is *not* the agent architecture
(which is replicable) or the LLM (which is rented). It is:
1. the **PIT dataset you accumulate from day one** — the only asset that compounds and
   cannot be bought later
2. the **Indian promoter/group/pledging knowledge graph** — genuinely underbuilt
3. the **decision-outcome-experience memory** the spec identifies as the unique IP
4. **multilingual Indian financial document understanding** — the one benchmark gap
   §16 exposes, and the strongest Sarvam-program angle

**As a trading business:** treat as unproven until the Alpha Validation Firewall has
rejected a few hundred hypotheses and the survivors have six months of live paper
evidence. The spec's own rules already encode this; the main risk is impatience with
them, not any technical gap.

**The two decisions to make before writing more code:**
1. **Personal tool or product?** It changes the licensing architecture (AGPL exposure
   from Fincept/OpenViking), the data-rights budget and whether SEBI RA/RIA registration
   is on the critical path.
2. **Do you start the PIT snapshot store this month?** Every month of delay is a month
   of the one dataset you cannot buy back.
