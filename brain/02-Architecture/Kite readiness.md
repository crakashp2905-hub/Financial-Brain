---
type: concept
tags:
  - concept
  - execution
  - safety
updated: 2026-09-23
---

# Kite readiness

Kite for **market data only**, never orders. That removes the objection this project has
been making: the argument was always against execution, not against data.

## What live data is actually for

It does not create an edge. It changes latency and realism, and nothing about
[[Twenty-five trades and no edge]] improves because the prices arrive faster. Two honest
reasons remain, and the second is the stronger:

**Minute bars.** Four of the five excluded strategies in [[Time-series strategies]] -
dual_thrust, r_breaker, ghost_trader, dynamic_breakout_ii - are intraday systems keyed off
the opening range. They are excluded today on a data fact: this archive is daily. Kite's
historical API serves minute bars, which makes them testable for the first time. That is a
concrete capability, not a vibe.

**End-to-end rehearsal.** Whether the loop produces a decision inside the session, with
point-in-time discipline intact and the safety gate consulted, under data that arrives
while it runs rather than sitting still in a table.

## The gates, before anything connects

1. **No order path exists.** Only read endpoints are implemented. Not "we choose not to
   call it" - the client does not carry the method. `PROPOSED_TO_BROKER` stays refused
   unless execution is explicitly enabled, and an agent still cannot reach
   `HUMAN_APPROVED`. See [[Phased autonomy]].
2. **Credentials stay the owner's.** Kite's login is a password and a 2FA step. Claude
   does not authenticate as the owner - the same boundary already drawn at MFCentral. The
   owner generates the access token; the code reads it from the environment and never
   stores it.
3. **A point-in-time leak test that runs against live capture.** Streaming data makes
   look-ahead easy and invisible: a tick that arrives during a decision must not be
   readable by it. [[The universe returned 84% a year]] is what a leak looks like when it
   hides in a filter rather than a signal, and that was in a *static* backtest.
4. **The control runs alongside.** Every live decision is scored against the same
   universe bought blindly, as in [[The control]]. Without it a rising market reads as
   skill.
5. **Rate limits respected.** Kite publishes them; exceeding them is both rude and a way
   to get the owner's account restricted.

## What it does not unlock

Paper only, and the [[C14 Investment Constitution]] still binds. Nothing here is a step toward
placing an order, and the readiness above does not become readiness for that. The question
"should real money be at risk" is a different question, with a different answer, and today
that answer is no - seven rejections, one void, and a committee measured at **-1.85% per
trade against buying the same names blindly**.

Related: [[Time-series strategies]] · [[Phased autonomy]] · [[The control]] ·
[[Situational awareness]] · [[Order validation chain]]
