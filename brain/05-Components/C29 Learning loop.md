---
type: component
phase: 5
status: built, not yet merged (2026-11-12)
depends-on:
  - "[[Learning from losses]]"
  - "[[C23 Strategy registry and paper trading]]"
tags:
  - component
  - validation
  - learning
---

# C29 Learning loop

**Never repeat a mistake the record supports - without learning noise.** Package `src/financial_brain/learning/`,
commands `fb learn ...`, full description in `docs/LEARNING-LOOP.md`.

> [!success] Built
> Trade autopsies from the quote archive, an incident ledger with guard coverage, a prospective hypothesis protocol, a shadow for skip
> rules, human-only approval, a paper live-v2 cohort, A/B, a terminal panel and a weekly digest. Nothing changes a live rule.

The shape follows from [[ADR-0004 one evidence standard]] ("a losing context becomes a hypothesis, never a fix"):

1. **Diagnose without statistics.** Every losing trade gets one primary cause from the path it took (never worked, time loss, noise stop,
   gave back, cost-dominated). Operational failures are incidents with a cause and a guard, and a guard that is not scheduled is reported as not
   active.
2. **Nominate, then confirm once on sessions it had not seen.** A pattern is nominated at fixed looks on everything so far, with its
   definition and window frozen; it is decided exactly once on the sessions after that. It claims *worse than the rest of the same day, and
   loses*, which is what stops one real cause from producing a dozen redundant lessons.
3. **State the error rate.** `fb learn calibrate` runs the same code over histories with no effect anywhere and with an effect planted: the
   chosen thresholds confirm nothing false in 150 histories and find a 40 bp loss about three times in four.
4. **A person decides.** Draft pre-registration, register, shadow (60 sessions), then `fb learn approve` at a terminal, bound to the exact rule,
   expiring in 180 days.
5. **Act in a separate paper cohort.** The engine's entry filter is proven by test to produce exactly live-v1 minus the declined entries.
6. **Measure** with A/B on the same days, a decay watch, and guard recurrence.

With the current record (13 live sessions) nothing is nominated; the status shows what it is watching and what needs the owner. That is the
honest default, and it is tested.

Related: [[Learning from losses]] - [[C23 Strategy registry and paper trading]] - [[ADR-0004 one evidence standard]]
