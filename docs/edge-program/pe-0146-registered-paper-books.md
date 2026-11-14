# PE-0146: Frozen strategy paper books

**Status: PARTIAL.** Added a hash-journaled allocation registry that freezes a
strategy ID, unique account ID, and INR starting capital per paper book. The
plan must exactly cover a frozen eligible-strategy list with an evidence ID.
It must be registered before its effective session, list unique accounts and
strategies, and remain within the approved total capital. Later plans must name
the preceding plan hash and a registered change ID. Account IDs cannot be
reassigned or resized; cumulative new-account capital cannot exceed the plan's
budget. The registry creates/reopens separate durable `PaperAccount` journals
and its scoped submit method rejects submitting one strategy through another
strategy's account. Cash transfer is explicitly disabled.

Validation: `python -m pytest -q tests/test_registered_paper_books.py` (7
passed) and Ruff passed. Tests cover restart, separate capital, reservations,
cross-book order rejection, immutable registration, successor plans, duplicate
accounts, budget limits, and disabled transfers.

Remaining work: connect this registry to the eligibility registry and route all
forward-session OMS calls through it; authenticate the registration/change
evidence through an approved owner identity service; reconcile account closure
and safely release capital before any reallocation. Until those integrations
exist, untrusted callers can bypass this registry by constructing `PaperAccount`
directly. No live or realized strategy capital is implied.
