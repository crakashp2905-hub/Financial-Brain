# Human decisions for manual paper sleeves

Date: 2026-11-24  
Checklist item: FB-1104  
Status: PARTIAL

## Implemented

`manual_paper_decisions.py` defines `APPROVE`, `REJECT`, `WATCH`, and `NO_TRADE` for a named human and one hashed candidate. Decisions are time-bounded and cannot outlive the candidate. A changed candidate becomes `STALE`; missing paper eligibility prevents approval. The append-only SQLite journal is idempotent across retries and restart. Its serialized decision fixes `execution_scope` to `PAPER_ONLY` and `physical_orders_authorized` to `false`; it has no broker or order-submission call.

## Evidence

- `src/financial_brain/forward_edge/manual_paper_decisions.py`
- `tests/test_forward_edge_manual_paper_decisions.py` — eight tests cover four actions, candidate hash binding, expiry, human-only actor, eligibility, durable/idempotent journal, and immutable rows.
- `python -m pytest tests/test_forward_edge_manual_paper_decisions.py -q --basetemp=.pytest-checklist-manual-approval-rerun` — 8 passed.
- Ruff and scoped mypy passed.

## Remaining scope

This is a typed journal API, not a complete trader interaction. It is not yet wired into the cockpit or a CLI approval flow, and `ForwardSessionRunner` does not consume these decisions to create a separate human-approved paper book. Therefore FB-1104 remains PARTIAL. No path here can authorize a physical cash order.
