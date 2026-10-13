---
type: decision
status: decided
tags:
  - decision
  - status/decided
---

# ADR-0005 broker path behind stages

Decided 2026-10-13 at the owner's instruction. Full text: `docs/adr/0005-broker-path-behind-stages.md`; runbook:
`docs/RUNBOOK-LIVE.md`; code: `execution/oms/`, `fb live`.

An order management system exists and is closed. KiteBroker sends an order only when all five locks are open:
`FB_LIVE_ORDERS=1` (owner's environment), a rollout stage armed by a human, a strategy verdict the stage accepts
(TICKET: CANDIDATE; SMALL_AUTO and SCALED: CONFIRMED under [[ADR-0004 one evidence standard]]), the broker kill
switch clear, and the pre-trade gate passing. Promotion is human-only and one step at a time against fixed
criteria; demotion is automatic. Limit orders only; every change journaled exactly once in a hash chain.

Supersedes the "no order path" claim in [[Kite readiness]]; extends [[Phased autonomy]] step 4.
