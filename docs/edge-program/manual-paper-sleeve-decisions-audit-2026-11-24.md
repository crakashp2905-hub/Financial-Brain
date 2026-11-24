# Human decisions for manual paper sleeves

Date: 2026-11-24  
Checklist item: FB-1104  
Status: PARTIAL

## Implemented

`manual_paper_decisions.py` defines `APPROVE`, `REJECT`, `WATCH`, and `NO_TRADE` for a named human and one hashed candidate. Decisions are time-bounded and cannot outlive the candidate. A changed candidate becomes `STALE`; missing paper eligibility prevents approval. The append-only SQLite journal is idempotent across retries and restart. Its serialized decision fixes `execution_scope` to `PAPER_ONLY` and `physical_orders_authorized` to `false`; it has no broker or order-submission call. `fb paper-decision` records a decision in the journal and emits the same paper-only boundary in its response.

## Evidence

- `src/financial_brain/forward_edge/manual_paper_decisions.py`
- `tests/test_forward_edge_manual_paper_decisions.py` — nine tests cover four actions, candidate hash binding, expiry, human-only actor, eligibility, durable/idempotent journal, immutable rows, and the CLI path.
- `python -m pytest tests/test_forward_edge_manual_paper_decisions.py -q --basetemp=.pytest-checklist-manual-approval-cli` — 9 passed.
- Ruff passed for the decision module, CLI and test. Scoped mypy passed for the decision module. Whole-file mypy on the legacy `cli.py` still reports unrelated errors outside the new command.

## Remaining scope

This records choices through the CLI, but is not yet wired into the cockpit or `ForwardSessionRunner`, and it does not create a separate human-approved paper book. The CLI trusts a self-declared human name; an identity provider is not present. Therefore FB-1104 remains PARTIAL. No path here can authorize a physical cash order.
