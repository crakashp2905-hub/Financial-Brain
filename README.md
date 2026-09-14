# Financial-Brain

An AI-native financial research system for Indian markets, in which the AI is the analyst
rather than the interface.

> LLM = reasoning and orchestration. Market data, fundamentals, filings, indicators and
> the investor's own philosophy = source of truth.
> `Data → Evidence → Context → Thesis → Risk → Decision → Outcome → Learning`

**Status:** design complete, implementation not started. First deliverable is Phase 0 —
the security master, bhavcopy ingestion and the point-in-time fundamentals store.

## Documentation

| Document | What it holds |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Design decisions, data and decision contracts, the four brains, agent model, validation gates, build order, the 15 non-negotiable rules |
| [docs/RESOURCES.md](docs/RESOURCES.md) | Every external resource — repos, data sources, models, datasets, benchmarks — with licence, role and a single verdict (INTEGRATE / WRAP / STUDY / REFERENCE / BUILD / REJECT) |
| [docs/FEASIBILITY-INDIA.md](docs/FEASIBILITY-INDIA.md) | Layer-by-layer feasibility scorecard, the five real blockers, what to cut or defer, India-adjusted build order |

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
