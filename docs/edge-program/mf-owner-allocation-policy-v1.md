# Owner-local MF/SIP allocation policy v1

`fb mf-policy` records an immutable, versioned owner preference profile in the local
paper database. It supports a cash reserve, maximum equity/debt/ETF fractions, a
rebalance band, and an optional SIP amount/frequency/day. A profile has an effective
interval and content hash; overlapping revisions and same-version edits are rejected.

The profile is keyed by a policy ID and a self-declared owner label. It contains no
broker account, folio ID, API credential, or live transaction permission. Its serialized
mode is `PAPER_DECISION_ONLY`, and the CLI always reports
`external_fund_transactions=false`. It does not create a SIP installment, allocate
funds, or access a registrar or broker.

This moves FB-0613 to **PARTIAL**. The policy is durable and owner-editable through a
local CLI, but the self-declared label is not authenticated, and the broader MF
recommendation/rebalance/SIP engines do not yet consume this policy as a shared active
allocation constraint. No live folio or transaction API is assumed.

Focused coverage: `tests/test_mf_allocation_policy.py` verifies persistence, as-of
visibility, immutable versions, invalid settings, absence of account/folio dependence,
and CLI help.
