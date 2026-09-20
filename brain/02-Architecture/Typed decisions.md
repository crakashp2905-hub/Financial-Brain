---
type: concept
tags:
  - concept
  - models
---

# Typed decisions

Software does not want prose. It wants **one of an allowed set of answers, with a
probability it can act on**. Every model in this system answers through that one shape:

    decide(state, choices) -> Decision(label, probs, confidence, model, latency)

- **rules** (Tier 0): a deterministic function, confidence 1.0 when a specific rule fired
- **FinBERT**: softmax over its three classes
- **local models via Ollama**: the choices are shown as lettered options, the model emits
  a single token, and the log-probabilities over those letters - renormalised to the
  allowed set - are the distribution. One forward pass, no text to parse, and never an
  answer outside the set.

TypeSafe's Jev popularised this shape in September 2026; it is cloud-only and waitlisted,
so the pattern was rebuilt locally with a slot left for it.

Raw probabilities are **not** calibrated and are never trusted as-is: the
[[Model router]] only uses the threshold measured per model, per label, per task, and
[[Calibration belongs to a prompt|per prompt]].

Related: [[Model router]] · [[C21 India evaluation benchmark]]
