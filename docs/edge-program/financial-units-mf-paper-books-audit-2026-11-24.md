# Mutual-fund amount, NAV, and unit typing in paper books

Date: 2026-11-24
Checklist item: FB-0103
Status: PARTIAL

## Change

The MF lot book now normalizes subscription amounts through `INRNotional`, NAV through `NavPerUnit`, and calculated or redeemed units through `MutualFundUnits`. The durable MF purchase allotment path accepts `NavPerUnit` and passes the validated nominal value into the lot book. Values are converted to decimals at the existing persistence boundary so existing journal schemas and idempotency hashes remain stable.

## Evidence

- `src/financial_brain/paper/mf_lot_book.py::subscribe`
- `src/financial_brain/paper/mf_lot_book.py::redeem`
- `src/financial_brain/paper/mf_purchase_book.py::apply_allotment`
- `tests/test_mf_lot_book.py::test_effective_dated_transaction_charges_reduce_units_and_redemption_proceeds`
- `tests/test_mf_purchase_book.py::test_purchase_freezes_eligible_date_stays_nav_pending_then_allots_exact_nav`
- `python -m pytest tests/test_mf_lot_book.py tests/test_mf_purchase_book.py -q --basetemp=.pytest-checklist-mf-units-rerun` — 21 passed.
- Ruff and scoped mypy passed for both MF modules and tests.

## Remaining scope

FB-0103 remains PARTIAL. SIP/systematic-flow, switch, account-ledger, NAV-ingestion, risk-sizing, and shared forward OMS serialization paths still need a repository-wide audit and nominal-type enforcement. Persisted schema values remain decimals, with unit identity enforced at these MF paper-book boundaries.
