---
type: component
phase: 2
status: done
depends-on:
  - "[[C25 Outcome attribution and calibration]]"
tags:
  - component
  - phase/2
  - validation
---

# Learning from losses

**Phase 2** · Never repeat a mistake the record actually supports

> [!success] Built
> `src/financial_brain/decisions/postmortem.py` (`fb lessons`).

The ask was reinforcement learning. With a few dozen closed trades that would fit noise
and then trade on it - the exact failure this system exists to prevent. What works at this
sample size is duller and checkable:

1. **Postmortem every closed trade, wins included.** A rule learned from losses alone
   also forbids the wins that share their features.
2. **Describe the entry in what was knowable then** - market regime, the filing type that
   surfaced the company, liquidity, whether a stated invalidation condition actually
   fired. This is where [[Situational awareness]] enters the record.
3. **Count before concluding.** A feature becomes a lesson only with >= 5 trades sharing
   it *and* a >= 2% gap in mean excess. Below that it is a story about two trades.
4. **Gate.** A confirmed lesson blocks the next such trade and says why: "6 of 6 lost,
   mean -6.0% against +4.0% elsewhere", with the decision ids attached so it can be
   argued with.

With the current record nothing is confirmed and the gate is inert. That is the honest
default, and it is tested.

Related: [[Own-record scorecard]] · [[First measured record]] · [[The unanswered question]]
