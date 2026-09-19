# ADR-0002 — Tiered models: deterministic first, small models by default, frontier by exception

**Status:** accepted · **Date:** 2026-09-19 · **Phase:** 2

## Context

The architecture's agents were drawn as if every one of them called a frontier LLM. At
Financial-Brain's volumes (≈1,300 BSE announcements a day, 3.1M in history) that is
expensive and mostly pointless: most of the work is classification and extraction with
a fixed answer set. The owner asked (2026-09-19) for a tiered design in which small
specialised models do the repetitive work and a frontier model is called only when the
problem needs deep reasoning; for FinGPT/FinBERT/Fin-R1-class models to be evaluated;
and for the Jev ("System One", TypeSafe AI, Sept 2026) pattern - typed answers with
probabilities - to be used or rebuilt.

## Decision

1. **Tier 0 — no model.** Anything computable is computed: prices, factors, costs,
   Sharpe, regimes, corporate actions, rule-based classification. An LLM never
   calculates.
2. **Every model call is a typed decision.** `llm/system1.py`: `decide(state, choices)
   → label, probabilities, confidence`. Local LLMs answer with one option letter and
   the probability comes from Ollama's token log-probabilities; FinBERT from its
   softmax; rules from which rule fired. No free text is parsed; no answer can fall
   outside the allowed set. Jev slots in behind the same interface once the owner has
   access (it is waitlisted, cloud-only).
3. **Tiers.** 1: tiny/encoder (≤3B, FinBERT); 2: local 7–14B; 3: cheap cloud; 4:
   frontier. Local models run on the owner's laptop through Ollama (CPU only, 16 GB).
   Cloud tiers stay behind the existing gate (`FB_LLM_ENABLED=1`).
4. **Models qualify by measurement.** `evaluation/models.py` benchmarks every candidate
   on Financial-Brain's own labelled tasks (Indian filings, not US news) and records
   accuracy, macro-F1, latency on this machine, and a **calibrated acceptance
   threshold** - the confidence above which the model's answers meet the task's target.
   A model whose confidence carries no information gets no threshold and is never
   routed to, however good its average.
5. **The router** (`llm/router.py`) tries models cheapest-first (tier, then latency) and
   accepts the first answer that clears that model's threshold; otherwise it escalates;
   if nothing clears, the answer is returned marked *uncertain*. Every call is recorded
   with task, tier, latency and cost.
6. **The frontier model is a chair, not a worker** - for synthesis over results the
   lower tiers and Tier 0 produced (committee, deep research), never for bulk work.

## First measurements (2026-09-19, see `model_bench`)

* Event type: **rules 98.9%** on 270 labelled announcements - Tier 0 wins; LLMs are only
  worth trying on what the rules mark uncertain.
* Sentiment (165 Claude-labelled Indian filing headlines): **FinBERT 80% accuracy but
  macro-F1 0.45** - it calls 17 of 19 adverse filings neutral. Reputation is not a
  qualification; local LLM results are in `data/bench_sentiment.log`.

## Consequences

* Adding a model is a registry line plus a benchmark run; nothing else changes.
* Labelled Indian task sets are now a core asset (`tests/fixtures`). They were labelled
  by Claude and should be spot-checked by the owner.
* Licences: FinBERT code is Apache-2.0 (github.com/ProsusAI/finBERT); Fin-R1 lists no
  licence on Hugging Face - fine for personal use (D1), verify before any product use.
* Not adopted: training our own model now. Fine-tuning (LoRA on Qwen-class models with
  FinGPT-style data plus our labelled Indian sets) becomes worthwhile only after the
  benchmark shows a gap no available model closes.
