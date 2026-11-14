# PE-0152 — Calendar-backed multi-session holding and exits

## Implemented

- Added an immutable exchange-session calendar contract with source identity and coverage validation. Holding horizons are counted from the entry session using exchange sessions, so midnight, weekends, and holidays do not reset or shorten the clock.
- The daily paper-book adapter can resolve a frozen exit session and exposes its schedule, calendar identity, remaining sessions, and SCHEDULED/DUE/OVERDUE state to the forward runner. It refuses missing calendar coverage, inconsistent stored held-session counts, and a clock that predates the scheduled exchange session.
- Due and overdue positions become deterministic SELL-to-close order intents with original position identity and quantity. An overdue exit retains its original order identity while reporting its overdue reason; it is not silently rolled to a new horizon.
- Exit window is explicitly EXCHANGE_CLOSE; the schedule itself never hard-codes a same-day 15:15 intraday flat rule.

## Verification

`tests/test_forward_book_adapter.py` and `tests/test_forward_paper_account_bridge.py`: 13 passed; Ruff passed. Coverage includes weekend traversal, scheduled/due/overdue dates, deterministic SELL-to-close intents, missing calendar horizon, non-session as-of date, stale schedule clock rejection, and accounting reconciliation of canceled partial fills with reservation release.

## Remaining evidence gap

The exchange calendar is caller-supplied and its authoritative provider feed has not been wired. The scheduled intents are exposed to the adapter layer but are not yet bound automatically to all strategy/session risk, execution, and accounting paths. No live forward overnight session is claimed; this item remains PARTIAL.
