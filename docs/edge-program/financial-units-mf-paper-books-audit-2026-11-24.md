# Mutual-fund amount, NAV, and unit typing in paper books

Date: 2026-11-24
Checklist item: FB-0103
Status: PARTIAL

## Change

The MF lot book now normalizes subscription amounts through `INRNotional`, NAV through `NavPerUnit`, and calculated or redeemed units through `MutualFundUnits`. The durable MF purchase allotment path accepts `NavPerUnit` and passes the validated nominal value into the lot book. Values are converted to decimals at the existing persistence boundary so existing journal schemas and idempotency hashes remain stable.

SIP installments now accept `INRNotional`, `NavPerUnit`, and `MutualFundUnits` at the immutable lifecycle model boundary. The model validates each nominal unit and normalizes it to `Decimal` before persistence and reconciliation, preserving the existing serialized schema.

## Evidence

- `src/financial_brain/paper/mf_lot_book.py::subscribe`
- `src/financial_brain/paper/mf_lot_book.py::redeem`
- `src/financial_brain/paper/mf_purchase_book.py::apply_allotment`
- `src/financial_brain/kernel/mf_sip.py::MFSIPInstallment.__post_init__`
- `tests/test_mf_sip.py::test_sip_accepts_nominal_inr_nav_and_fractional_units_at_model_boundary`
- `tests/test_mf_lot_book.py::test_effective_dated_transaction_charges_reduce_units_and_redemption_proceeds`
- `tests/test_mf_purchase_book.py::test_purchase_freezes_eligible_date_stays_nav_pending_then_allots_exact_nav`
- `python -m pytest tests/test_mf_lot_book.py tests/test_mf_purchase_book.py -q --basetemp=.pytest-checklist-mf-units-rerun` — 21 passed.
- `python -m pytest tests/test_mf_flow_book.py tests/test_mf_sip_book.py tests/test_mf_systematic_flow_book.py -q --basetemp=.pytest-checklist-mf-callers` — 23 passed through existing MF callers.
- `python -m pytest tests/test_mf_sip.py tests/test_mf_sip_book.py -q --basetemp=.pytest-checklist-mf-sip-units` — 16 passed.
- Ruff passed for MF lot/purchase/SIP modules and tests. Scoped mypy passed for MF lot/purchase/SIP modules.

## Remaining scope

FB-0103 remains PARTIAL. Systematic-flow orchestration, switch, account-ledger, NAV-ingestion, risk-sizing, and shared forward OMS serialization paths still need a repository-wide audit and nominal-type enforcement. Persisted schema values remain decimals, with unit identity enforced at the validated MF paper-book and SIP-model boundaries.
