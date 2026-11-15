# PE-0150: Pre-trade gates and durable skip attribution

**Status: PARTIAL.** Added a frozen, content-addressed policy evaluator for
quote freshness/spread, displayed quantity and dated ADV capacity, costed net
alpha, projected exposure, and model confidence. Its risk-verdict adapter
provides the full named checks and source evidence identities. The forward
session runner now records every explicit `NO_TRADE`/`REJECTED` and missed or
rejected execution as an immutable `PAPER_SKIP`, including stable candidate or
intent identity, signal/input hashes, failed risk checks, preflight, execution
reason, and decision details. Replays must reproduce the same skip record.

Registered `ForwardPaperSessionService` sessions can now bind a policy digest in
`ForwardRunConfig`. When that digest is present, the service requires the exact
policy and its evidence adapter, verifies strategy/version identity, supplies
only decision-visible quotes to the adapter, requires the snapshot quote to hash
to one of those journaled inputs, checks snapshot identity against the sealed
order intent, and combines the pre-trade verdict with existing risk.
Missing, changed, or malformed policy evidence blocks the order; the persisted
risk verdict carries the policy hash and gate checks.

Validation: focused pre-trade, registered paper bridge, decision sealing, and
session-runner suites pass; Ruff and mypy pass for the changed modules.

The policy is mandatory only for sessions whose frozen config declares its hash;
other callers can still omit it. The repository does not yet bind every strategy
session to these gates or provide one authoritative ADV source, frozen cost
table, portfolio exposure book, calibrated confidence record, or eligibility
registry. Those inputs and the callback must be connected before claiming every
paper session applies these gates. Skip records are durable even for sessions
that abstain before an order is formed.
