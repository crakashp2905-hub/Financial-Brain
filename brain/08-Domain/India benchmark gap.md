---
type: concept
tags:
  - concept
  - research
  - india
---

# India benchmark gap

Every dataset in the [[FinLLMs survey]] is English and US/EU-centric — FPB, FiQA, FOMC,
FinQA, ECTSum, StockNet. **There is no Indian equivalent.**

Consequences:
1. We cannot evaluate Indian financial NLP against any published baseline — we must build
   [[C21 India evaluation benchmark]]
2. **[[RBI hawkish dovish dataset]] is the Indian FOMC task** — build it first
3. Indian regional-language financial text is essentially unbenchmarked worldwide — an
   open research gap *and* the stated differentiator
4. Indian earnings calls carry accent and code-switching that off-the-shelf ASR handles
   worse than US calls — a real cost on the multimodal path
