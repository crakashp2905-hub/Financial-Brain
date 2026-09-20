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

## Routing result (2026-09-20) - and the correction that followed

Thresholds are **per predicted label** (a model's "neutral" must itself be right >= 90%
of the time when confident, min. 3 examples): overall accuracy let a model that says
"neutral" to everything qualify on a neutral-heavy set.

The first route was chosen *and judged* on the same 165 items:
`finbert -> llama3.2:3b -> llama3.1:8b`, 89.1% at 1.26 s/item. A held-out set was then
labelled (104 headlines, 2021-2023, positive-enriched, labelled before any model saw
them) and that route scored **65.4%**, accepting a wrong answer **23.6%** of the time,
with zero recall on positives. In-sample calibration is worthless.

Route selection therefore **requires out-of-sample verification**
(`fb models route --verify-on sentiment_holdout`): candidates are ranked on the tuning
set, then each is replayed on the held-out set and kept only if its accepted answers are
wrong at most 10% of the time. Result:

| chain | tuning | held out |
|---|---|---|
| finbert -> llama3.2:3b -> llama3.1:8b | 89.1%, 1.26 s | 65.4%, **23.6% wrong when accepted** - rejected |
| finbert -> qwen2.5:1.5b -> llama3.1:8b | - | 18.6% wrong when accepted - rejected |
| **qwen2.5:7b alone** | 85.5%, 5.3 s | **86.5%, 7% wrong when accepted**, declines 34% - **stored** |

**Fin-R1 (2026-09-20, once the model store moved to D:).** It had been marked
`system_one = false` on the assumption that an R1-style distill must think before
answering. Measurement says otherwise: forced to a single token it answers on-menu in
~2.4-3.7 s, and it is the only local model that earns a threshold for **all three**
labels, "positive" included. Leading the route it settles 82% of filings by itself:

| route (error bar) | tuning | held out |
|---|---|---|
| **fin-r1 -> llama3.1:8b -> gemma2** (10%) | 90.9% | **89.4%, 9.8% wrong when accepted, declines 2%**; recall 94% neutral / 75% positive / 80% negative |
| llama3.1:8b -> phi4 -> gemma2 (15%) | 91.5% | 85.6%, 14.6% wrong when accepted, declines 1%; negative recall 60% |

The Fin-R1 chain is deployed: it passes the *strict* bar the previous route could not,
is 1.2 s/item faster, and finds four adverse filings in five. Finance-specific training
is worth something after all - but only the kind that survives our own labels. FinBERT
(F1 0.45) and FinSenti (0.05) carry the same "financial" billing and failed.

With every model benchmarked on the held-out set too, the earlier comparison was

| route | tuning | held out |
|---|---|---|
| qwen2.5:7b alone (error bar 10%) | 85.5% | 86.5%, 7% wrong when accepted, **declines 34%**, flags nothing but "neutral" |
| **llama3.1:8b -> phi4 -> gemma2** (error bar 15%) | 91.5% | **85.6%, 14.6% wrong when accepted, declines 1%**; recall 75% of positives, 60% of negatives |

The second is deployed: a tone *hint* in the brief is worth more at 85% precision with
coverage than at 93% precision that never flags anything. It is labelled with the model
that produced it and is MODEL-tier evidence - never a fact. The cheap-first cascades
remain rejected; escalation here runs big-to-bigger, and 88% of items are settled by the
first model. Cost: ~6.8 s per filing on this CPU, so the daily step is capped.

## Consequences

* Adding a model is a registry line plus a benchmark run; nothing else changes.
* Labelled Indian task sets are now a core asset (`tests/fixtures`). They were labelled
  by Claude and should be spot-checked by the owner.
* Licences: FinBERT code is Apache-2.0 (github.com/ProsusAI/finBERT); Fin-R1 lists no
  licence on Hugging Face - fine for personal use (D1), verify before any product use.
* Not adopted: training our own model now. Fine-tuning (LoRA on Qwen-class models with
  FinGPT-style data plus our labelled Indian sets) becomes worthwhile only after the
  benchmark shows a gap no available model closes.
