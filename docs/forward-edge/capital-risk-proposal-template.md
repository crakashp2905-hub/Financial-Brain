# Micro-size capital and risk proposal

**Status:** PENDING HUMAN REVIEW. This document is a worksheet; it is not capital approval, a recommendation, or an order instruction. Physical ordering is disabled by repository build policy.

## Scope

- Proposal ID:
- Account alias (no credentials or account number):
- Strategy ID and frozen version:
- Review owner:
- Review date and expiry:
- Currency: INR
- Mode reviewed: PAPER / SHADOW / other
- Evidence bundle SHA-256:
- Cost schedule SHA-256:
- Source of current fee, tax, spread and impact assumptions:

## Proposed limits

| Limit | Proposed value (INR unless stated) | Evidence / rationale |
|---|---:|---|
| Paper capital currently reviewed | | |
| Proposed micro capital | | |
| Maximum per-order notional | | |
| Maximum risk per trade | | |
| Maximum daily loss | | |
| Maximum peak-to-trough drawdown | | |
| Maximum gross exposure | | |
| Maximum open positions | | |
| Maximum orders per session | | |
| Minimum ADV notional | | |
| Maximum spread (bps) | | |
| Estimated round-trip cost per trade | | |
| Minimum expected gross P&L per trade | | |
| Maximum cost / expected gross P&L ratio | | |
| Worst-case option loss and margin evidence | | |

Required inequalities: `risk per trade <= daily loss <= maximum drawdown <= proposed capital`; per-order notional must fit the proposed capital; round-trip costs must fit the explicitly stated expected P&L budget. If any value is unknown, the decision is **NO TRADE / NO APPROVAL**.

## Failure handling

- Stale feed, lost heartbeat, unknown broker state, accounting mismatch, or risk breach:
- Required response: latch DISARM, block new entries, preserve unknown orders for reconciliation.
- Reset evidence required:
- Named human who may approve reset:

## Human decision

- [ ] Broker/exchange permissions and product scope reviewed
- [ ] Cost assumptions checked against current evidence
- [ ] Liquidity and capacity checked at proposed size
- [ ] Margin, expiry, assignment and settlement reviewed for derivatives
- [ ] Maximum-loss and kill-switch behavior reviewed
- [ ] Existing paper evidence and prospective cohort reviewed
- Decision: APPROVE PAPER PROPOSAL / REJECT / REQUEST CHANGES
- Exact approved scope hash:
- Human approver:
- Approval timestamp and expiry:
- Separate CAPITAL approval reference:
- Separate live-mode approval (if applicable): **NOT AVAILABLE; physical ordering is disabled**

An approval of this worksheet cannot enable live trading. The corresponding versioned, machine-validated `CapitalRiskProposal` must also pass its risk and cost checks.
