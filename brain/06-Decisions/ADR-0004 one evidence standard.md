---
type: decision
status: decided
tags:
  - decision
  - status/decided
---

# ADR-0004 one evidence standard

Decided 2026-10-13. Full text: `docs/adr/0004-one-evidence-standard.md`; code: `evaluation/standard.py`.

One bar for every strategy: G1 pre-registered, G2 net of per-name costs, G3 |t| >= 2 with the right sign in both
2016-21 and 2022-26, G4 beats the 95th percentile of equal-size, equal-turnover random books, G5 out of sample
(>= 12 rebalances after the cutoff, live paper inside the backtest's band). G1-G4 make a CANDIDATE; only G5
makes it CONFIRMED. The Bonferroni bar is reported, no longer the gate.

At most 8 trials a calendar quarter, each appended to `docs/trials/ledger.jsonl`.

Registered under it: [[PRE-REGISTERED cost-aware momentum book]], [[PRE-REGISTERED short build-up avoid filter]],
[[PRE-REGISTERED earnings surprise drift out of sample]].
