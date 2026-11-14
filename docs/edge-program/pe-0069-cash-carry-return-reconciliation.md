# PE-0069 cash income and return reconciliation

The paper account now records sourced cash dividends with ex-date eligibility, payment date, actual application date, gross income, and withholding. Residual cash yield is posted from a sourced annual rate over an effective interval, using reconstructed daily closing cash. Cash-changing fills and standalone fees without dates cause the yield calculation to fail closed. Yield periods cannot overlap, and income cannot be applied retroactively across a captured NAV snapshot.

`reconcile_total_return()` bridges NAV change after external cash flows to realized and unrealized price P&L, gross dividend and cash-yield income, fees, and tax. It requires position marks and allows at most ₹0.01 rounding difference. Carry is not yet incorporated into the canonical paper account, and this implementation does not source or validate market rates, dividend calendars, or payments.

The task remains `PARTIAL`: tests use synthetic records; live or owner-supplied income evidence and independent reconciliation are still required.
