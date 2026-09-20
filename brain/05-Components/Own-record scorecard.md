---
type: component
phase: 2
status: done
depends-on:
  - "[[C23 Strategy registry and paper trading]]"
tags:
  - component
  - phase/2
  - validation
---

# Own-record scorecard

**Phase 2** · The system's own calls, scored - or honestly unscored

> [!success] Built
> `src/financial_brain/evaluation/scorecard.py` (`fb scorecard`).

A research system that never scores itself gets more articulate, not more right. Closed
paper trades are summarised with:

- **hit rate with a Wilson interval** - eight wins from twelve is 67% and the interval
  still includes a coin;
- **mean excess** against the Nifty, net of the round-trip cost charged;
- **slices** by action and liquidity, because an edge that appears only in illiquid names
  is usually the cost model's illusion.

Below **20 closed trades** the verdict is "not enough evidence yet", whatever the wins
look like. The brief prints the record even when empty, because a reader is entitled to
know the calls above have never been scored.

Today it reads: *no decision this system can be scored on.*

Related: [[The unanswered question]] · [[C25 Outcome attribution and calibration]] ·
[[Alpha Validation Firewall]]
