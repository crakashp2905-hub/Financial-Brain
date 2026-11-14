# PE-0170 — Family evidence gate for paper automation

## Acceptance

Each strategy family must pass explicit evidence gates before its initial paper allocation, later allocation increase, or paper automation is enabled. Promotion is tied to a frozen protocol established before the cohort, a minimum of 60 verified sessions with complete receipts, an explicit candidate denominator, multiplicity-adjusted analysis, cost stress, positive matched-control and net-alpha lower confidence bounds, and a current human approval scoped to the exact family, action, capital and paper mode. A new strategy cannot inherit another family's approval or bypass the frozen-protocol requirement.

## Implementation and verification

`financial_brain.forward_edge.governance.evaluate_family_action` evaluates family-specific initial-allocation, capital-increase and paper-automation requests. `RegisteredPaperBooks` persists evidence and approval with each frozen plan, requires exact authorizations for each newly funded strategy account, and revalidates them when plans are restored. It rejects unsupported increases in the cash budget. The generic `authorize_economic_change(CAPITAL, ...)` path is refused. `ForwardRunConfig` carries family evidence and owner approval in its canonical identity. `ForwardSessionRunner` re-evaluates the automation gate for every paper BUY/SELL decision and persists the verdict in the risk record before it can append a `PAPER_INTENT` or outbox item. Missing evidence, failed criteria, absent, expired, stale or non-human approval blocks the allocation or order. Replay/shadow research behavior is unaffected; live execution remains unavailable through this gate.

Verification: `tests/test_forward_session_runner.py`, `tests/test_forward_edge_f11.py`, `tests/test_registered_paper_books.py`, `tests/test_source_inventory.py` and `tests/test_strategy_variant_inventory.py` — 90 passed. Ruff passed. Targeted mypy passed for `session_runner.py`, `governance.py` and `registered_paper_books.py`. Both inventory freshness checks passed (617 Python files; 253 source-linked candidates).

## Limits

Tests use synthetic family evidence and synthetic owner approvals. They prove enforcement and scope binding, not that any real family has 60 eligible sessions, positive net alpha, or valid owner authorization. The registry enforces the declared evidence fields and approval scope; an authorized human must still review the referenced evidence manifest. Real paper automation/allocation will remain blocked until operators supply reviewed, hash-bound family evidence and valid human approvals. This does not authorize live broker orders or capital.
