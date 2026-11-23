# Mutual-fund amount, NAV, and unit typing in paper books

Date: 2026-11-24
Checklist item: FB-0103
Status: PARTIAL

## Change

The MF lot book now normalizes subscription amounts through `INRNotional`, NAV through `NavPerUnit`, and calculated or redeemed units through `MutualFundUnits`. The durable MF purchase allotment path accepts `NavPerUnit` and passes the validated nominal value into the lot book. Values are converted to decimals at the existing persistence boundary so existing journal schemas and idempotency hashes remain stable.

SIP installments now accept `INRNotional`, `NavPerUnit`, and `MutualFundUnits` at the immutable lifecycle model boundary. The model validates each nominal unit and normalizes it to `Decimal` before persistence and reconciliation, preserving the existing serialized schema.

Recurring SWP/STP instructions and events accept nominal amount, NAV, and fractional-unit values, and paired MF switch requests/events accept typed units and INR values. Both lifecycles normalize typed inputs before existing reconciliation logic and durable serialization.

## Evidence

- `src/financial_brain/paper/mf_lot_book.py::subscribe`
- `src/financial_brain/paper/mf_lot_book.py::redeem`
- `src/financial_brain/paper/mf_purchase_book.py::apply_allotment`
- `src/financial_brain/kernel/mf_sip.py::MFSIPInstallment.__post_init__`
- `tests/test_mf_sip.py::test_sip_accepts_nominal_inr_nav_and_fractional_units_at_model_boundary`
- `src/financial_brain/kernel/mf_systematic_flows.py::MFSystematicFlowInstruction` and `MFSystematicFlowEvent`
- `src/financial_brain/kernel/mf_switch.py::MFSwitchRequest` and `MFSwitchEvent`
- `tests/test_mf_systematic_flows.py::test_systematic_flows_accept_nominal_inr_nav_and_fractional_unit_values`
- `tests/test_mf_switch.py::test_switch_accepts_nominal_mf_units_and_inr_nav_values`
- `tests/test_mf_lot_book.py::test_effective_dated_transaction_charges_reduce_units_and_redemption_proceeds`
- `tests/test_mf_purchase_book.py::test_purchase_freezes_eligible_date_stays_nav_pending_then_allots_exact_nav`
- `python -m pytest tests/test_mf_lot_book.py tests/test_mf_purchase_book.py -q --basetemp=.pytest-checklist-mf-units-rerun` — 21 passed.
- `python -m pytest tests/test_mf_flow_book.py tests/test_mf_sip_book.py tests/test_mf_systematic_flow_book.py -q --basetemp=.pytest-checklist-mf-callers` — 23 passed through existing MF callers.
- `python -m pytest tests/test_mf_sip.py tests/test_mf_sip_book.py -q --basetemp=.pytest-checklist-mf-sip-units` — 16 passed.
- `python -m pytest tests/test_mf_systematic_flows.py tests/test_mf_systematic_flow_book.py -q --basetemp=.pytest-checklist-mf-systematic-units` — 16 passed.
- `python -m pytest tests/test_mf_switch.py tests/test_mf_switch_book.py -q --basetemp=.pytest-checklist-mf-switch-units` — 8 passed.
- Ruff passed for MF lot/purchase/SIP/systematic-flow/switch modules and tests. Scoped mypy passed for those kernel and paper modules.

## Remaining scope

FB-0103 remains PARTIAL. MF account-ledger, NAV-ingestion, risk-sizing, shared forward OMS/accounting serialization, and a repository-wide boundary audit still need nominal-type enforcement. Persisted schema values remain decimals, with unit identity enforced at validated MF paper-book, SIP, systematic-flow, and switch model boundaries.
