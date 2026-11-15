# PE-0152 — Calendar-backed multi-session holding and exits

## Implemented

- Added an immutable exchange-session calendar contract with source identity and coverage validation. Holding horizons are counted from the entry session using exchange sessions, so midnight, weekends, and holidays do not reset or shorten the clock.
- The daily paper-book adapter can resolve a frozen exit session and exposes its schedule, calendar identity, remaining sessions, and SCHEDULED/DUE/OVERDUE state to the forward runner. It refuses missing calendar coverage, inconsistent stored held-session counts, and a clock that predates the scheduled exchange session.
- Due and overdue positions become deterministic SELL-to-close order intents with original position identity and quantity. An overdue exit retains its original order identity while reporting its overdue reason; it is not silently rolled to a new horizon.
- Exit window is explicitly EXCHANGE_CLOSE; the schedule itself never hard-codes a same-day 15:15 intraday flat rule.
- `ForwardPaperSessionService` now recognizes visible due/overdue `BOOK_POSITION` events and routes a single close through the frozen decision, registered risk callback, quote simulator, durable order lifecycle, and cash-account reconciliation. It checks that the event is available by decision time, the instrument is in the frozen universe, and event quantity exactly matches account inventory. Quantity or event mismatches fail closed.
- Explicit `REDUCE`/`CLOSE` sells bypass entry-only net-alpha, pretrade, and family-enrollment gates; feed-health and the configured risk callback still apply. The current single-intent runner refuses multiple due exits in one session rather than silently dropping positions.

## Verification

`tests/test_forward_paper_account_bridge.py`, `tests/test_forward_book_adapter.py`, `tests/test_forward_session_runner.py`, and `tests/test_forward_feed_health.py`: 91 passed; Ruff and mypy passed. In addition to calendar boundary cases, the end-to-end service test opens a registered paper position and closes it from a due schedule through simulated execution and account reconciliation, while verifying a fresh strategy entry callback is not used.

## Remaining evidence gap

The exchange calendar and `BOOK_POSITION` event construction are still caller-supplied; no authoritative effective-dated calendar provider is wired. The runner supports one intent per session, so a portfolio with multiple due exits must be orchestrated across sessions or routed through a future multi-intent runner. No live forward overnight session is claimed; this item remains PARTIAL.
