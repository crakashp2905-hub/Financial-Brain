# Financial unit types at the cash paper-account boundary

Date: 2026-11-24  
Checklist item: FB-0103  
Status: PARTIAL

## Change

The durable cash paper account now converts legacy cash-equity inputs into the existing nominal unit types before writing journal events. `submit_typed_intent` accepts `WholeUnits`, `Price`, and `Money`; it refuses fractional or nonpositive quantities, reserve currencies that differ from the account, and limit prices in another currency. `record_typed_fill` applies the same whole-unit and account-currency checks to fills. Existing integer/Decimal method signatures remain as compatibility boundaries and route through the typed methods.

## Evidence

- `src/financial_brain/kernel/paper_account.py`
- `tests/test_paper_account.py::test_typed_cash_order_and_fill_units_are_enforced`
- Focused test command: `python -m pytest tests/test_paper_account.py -q --basetemp=.pytest-checklist-units` — 8 passed.

The first test attempt used the host's default pytest temp folder outside the writable workspace and failed while creating preview journal files. Re-running with an in-worktree base temp directory passed.

## Remaining scope

FB-0103 remains PARTIAL. The nominal types exist for option lots, mutual-fund fractional units, NAV, option premium, and basis points, but integration through option order/risk, MF allotment, forward paper OMS, and serialized account state is not demonstrated by this change. The cash book remains a long-only cash instrument model and does not model derivatives or MF units.
