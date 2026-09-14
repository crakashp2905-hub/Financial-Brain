---
type: concept
tags:
  - concept
  - research
  - model
---

# Model strategy

From the [[FinLLMs survey]] technique ladder:

| Technique | Exemplar | Us |
|---|---|---|
| Continual pre-training | FinBERT-19 | — |
| Domain pre-training from scratch | FinBERT-20 | ❌ infeasible |
| Mixed-domain pre-training | FinBERT-21, FLANG | ❌ |
| Mixed-domain LLM + prompting | BloombergGPT | ❌ cost |
| **Instruction-tuned LLM + prompting** | [[FinGPT]], FinMA, InvestLM | ✅ **the path** |

Plus small FinBERT-class encoders for narrow high-volume classification (sentiment
tagging of news and filings) where an LLM call per document is wasteful.

**Never pre-train.**
