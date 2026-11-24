# Mutual-fund amount, NAV, and unit typing in paper books

Date: 2026-11-24
Checklist item: FB-0103
Status: PARTIAL

## Change

The MF lot book now normalizes subscription amounts through `INRNotional`, NAV through `NavPerUnit`, and calculated or redeemed units through `MutualFundUnits`. The durable MF purchase allotment path accepts `NavPerUnit` and passes the validated nominal value into the lot book. Values are converted to decimals at the existing persistence boundary so existing journal schemas and idempotency hashes remain stable.

SIP installments now accept `INRNotional`, `NavPerUnit`, and `MutualFundUnits` at the immutable lifecycle model boundary. The model validates each nominal unit and normalizes it to `Decimal` before persistence and reconciliation, preserving the existing serialized schema.

Recurring SWP/STP instructions and events accept nominal amount, NAV, and fractional-unit values, and paired MF switch requests/events accept typed units and INR values. Both lifecycles normalize typed inputs before existing reconciliation logic and durable serialization.

Risk sizing accepts typed INR budgets/capital limits, whole-unit displayed depth, and `BasisPoints` participation caps. Basis points convert explicitly to the existing rate representation before quantity calculation.

The MF paper ledger accepts `INRNotional` on monetary journal lines and a signed `MutualFundUnitDelta` on unit journal lines. Existing decimal journal schemas and idempotency payloads remain canonical after normalization.

AMFI NAV ingestion validates each received row as a positive `NavPerUnit` before writing the as-known observation and legacy projection. The decimal storage contract remains intact; zero or negative NAV is refused.

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
- `src/financial_brain/kernel/risk_sizing.py::size_trade_plan`
- `tests/test_risk_sizing.py::test_sizing_accepts_nominal_inr_depth_and_basis_point_limits`
- `src/financial_brain/paper/mf_account_ledger.py::post_journal`
- `src/financial_brain/kernel/units.py::MutualFundUnitDelta`
- `tests/test_mf_account_ledger.py::test_journal_accepts_typed_inr_and_signed_mutual_fund_unit_deltas`
- `src/financial_brain/ingest/mfnav.py::ingest`
- `tests/test_mfnav_revisions.py::test_nav_ingest_rejects_nonpositive_nav_before_persisting_observation`
- `tests/test_mf_lot_book.py::test_effective_dated_transaction_charges_reduce_units_and_redemption_proceeds`
- `tests/test_mf_purchase_book.py::test_purchase_freezes_eligible_date_stays_nav_pending_then_allots_exact_nav`
- `python -m pytest tests/test_mf_lot_book.py tests/test_mf_purchase_book.py -q --basetemp=.pytest-checklist-mf-units-rerun` — 21 passed.
- `python -m pytest tests/test_mf_flow_book.py tests/test_mf_sip_book.py tests/test_mf_systematic_flow_book.py -q --basetemp=.pytest-checklist-mf-callers` — 23 passed through existing MF callers.
- `python -m pytest tests/test_mf_sip.py tests/test_mf_sip_book.py -q --basetemp=.pytest-checklist-mf-sip-units` — 16 passed.
- `python -m pytest tests/test_mf_systematic_flows.py tests/test_mf_systematic_flow_book.py -q --basetemp=.pytest-checklist-mf-systematic-units` — 16 passed.
- `python -m pytest tests/test_mf_switch.py tests/test_mf_switch_book.py -q --basetemp=.pytest-checklist-mf-switch-units` — 8 passed.
- `python -m pytest tests/test_risk_sizing.py -q --basetemp=.pytest-checklist-risk-units` — 5 passed.
- `python -m pytest tests/test_mf_account_ledger.py -q --basetemp=.pytest-checklist-mf-ledger-units-rerun` — 6 passed.
- `python -m pytest tests/test_mfnav_revisions.py -q --basetemp=.pytest-checklist-nav-units-rerun` — 7 passed.
- Ruff passed for MF lot/purchase/SIP/systematic-flow/switch/risk-sizing/ledger/NAV modules and tests. Scoped mypy passed for those kernel, ingest, and paper modules.

## Remaining scope

FB-0103 remains PARTIAL. Risk-sizing result and shared forward OMS/accounting serialization, plus a repository-wide boundary audit still need nominal-type enforcement. Persisted schema values remain decimals, with unit identity enforced at validated MF NAV-ingest, paper-book, account-ledger, SIP, systematic-flow, switch model, and risk-sizing input boundaries.
