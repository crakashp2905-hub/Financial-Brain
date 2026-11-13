# Forward session and paper account bridge

`ForwardPaperSessionService` requires a caller-supplied preflight callback,
then records that immutable result before running the forward session. An
invalid or throwing callback is converted to a fail-closed preflight record;
exception text and credential values are not persisted. A captured preflight is
reused on restart so a changing callback cannot rewrite the session's gate.

After a completed simulated receipt, `reconcile_paper_account` posts a stable
OMS intent and each receipt fill to the cash-equity `PaperAccount`. Stable
intent and fill IDs make replay idempotent if a process stops between an
account event and the runner's cross-journal reconciliation record. A BUY
requires an explicit `account_cash_reserve_inr` in the persisted approved risk
verdict. The bridge requires an effective-dated fee schedule and records fees,
cash, available cash, OMS state, inventory, realized P&L, and an optional
conservative liquidation-bid mark for unrealized P&L.

This bridge supports long-only INR cash-equity accounts. The account model does
not provide derivatives margin, short inventory, settlement-cycle accounting,
or option exercise/assignment. A paper-account rejection is surfaced as a
reconciliation failure; it does not alter or relabel the runner's simulated
receipt. No broker order path is present.
