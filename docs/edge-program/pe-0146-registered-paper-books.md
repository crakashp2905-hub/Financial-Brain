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

Validation: the registered-book and forward paper bridge suites pass together
(29 tests); Ruff and mypy pass for the changed bridge. In addition to restart,
capital isolation, and cross-book order rejection, the live-paper service now
requires a registered plan, resolves the exact strategy allocation at the
decision timestamp, and checks both account ID and opening capital before
preflight or session writes. Receipt reconciliation submits the order through
the registered-book family evidence and owner-approval gate. A mismatched
opening-capital regression confirms rejection without writing to the account.

Remaining work: authenticate the registration/change evidence through an
approved owner identity service; reconcile account closure and safely release
capital before any reallocation; wire any legacy paper-account writers through
this registry. Direct construction of `PaperAccount` remains possible outside
the guarded forward paper service. No live or realized strategy capital is
implied.
