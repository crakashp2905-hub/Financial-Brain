# Mutual-fund action decisions v1.0.0

**Checklist item:** FB-1108  
**Status:** DECISION-ONLY WORKFLOW IMPLEMENTED, CONSUMER AUTHENTICATION/ORDER INTEGRATION INCOMPLETE

## User-facing decision model

The MF paper panel explains five choices as decisions, not current-price transaction buttons:

- `SUBSCRIBE`: a one-time intended INR allocation; units depend on the scheme's eligible NAV and payment/cutoff rules.
- `REDEEM`: a folio-specific intent for an amount or units; eligible NAV and payout settlement are determined later.
- `SWITCH`: a source/destination scheme intent with both scheme identities and amount or units.
- `SIP`: a recurring schedule with scheme identity, INR amount, frequency, and schedule dates; installments remain individually funded and NAV-dated.
- `HOLD`: an explicit no-transaction decision with a reason.

`fb mf-decision` records one human-selected choice in the DuckDB `mf_action_decisions` journal. Records bind a decision-time context SHA-256, actor label, reason, timestamps, expiry, scheme/account details, and a content hash. Writes are idempotent by decision ID and conflicting ID reuse is refused. Expired choices remain visible as `EXPIRED` when rendered for a later as-of date.

Every serialized record says `execution_scope=PAPER_DECISION_ONLY` and `physical_orders_authorized=false`. The journal does not call an MF order API, create an `mf_paper_order`, reserve cash/units, or imply AMC/RTA acceptance. A later explicit, separate paper-order workflow is required to model a transaction.

## Limits and remaining work

The actor identity is self-declared by the local CLI; there is no authenticated user/session, signed approval, multi-user permission check, or UI form that submits decisions. The CLI accepts a context hash from its caller; it does not independently reconstruct or attest the consumer screen snapshot. This is a local paper decision journal, not a transaction channel or a suitability determination.

## Verification

- `tests/test_mf_decisions.py` covers all five action shapes, field validation, expiry, idempotency/conflict detection, as-of visibility, the CLI record path, the terminal decision language, and the no-order invariant.
- Existing terminal MF book tests still pass in the focused run. Ruff and scoped mypy pass for the new decision module.
