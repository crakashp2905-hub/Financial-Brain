# Financial-Brain

An AI-native financial research system for Indian markets, in which the AI is the analyst
rather than the interface.

> LLM = reasoning and orchestration. Market data, fundamentals, filings, indicators and
> the investor's own philosophy = source of truth.
> `Data → Evidence → Context → Thesis → Risk → Decision → Outcome → Learning`

**Status:** Phase 0 and Phase 1 complete; Phase 2 well advanced (2026-09-20).

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

Next: see [docs/HANDOFF.md](docs/HANDOFF.md) and [docs/BUILD-FLOW.md](docs/BUILD-FLOW.md).

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
| [docs/adr/](docs/adr/) | Architecture decision records |
| [brain/](brain/) | Obsidian vault — the same material as a linked graph. Open `brain/` as a vault; start at `00-Meta/Home.md` |

## The short version

**First user:** a self-directed, long-term Indian-equity investor.
**First job:** understand what changed in my portfolio and watchlist, why it matters, and
whether any recorded thesis has strengthened or weakened.
**First product:** an Indian Equity Research & Portfolio Copilot — not a stock-tip engine.

**The edge is not the agent architecture** (replicable) **or the model** (rented). It is
the point-in-time dataset accumulated from day one, the Indian promoter/group/pledging
knowledge graph, the decision-outcome-experience memory, and multilingual Indian document
understanding.

## Decisions still open

1. Personal tool or commercial product? — changes AGPL exposure, data budget, and whether
   SEBI RA/RIA registration is on the critical path
2. Does the point-in-time snapshot store start this month? — it is the only dataset that
   compounds and cannot be bought back later

Full list in [docs/RESOURCES.md §11](docs/RESOURCES.md).
