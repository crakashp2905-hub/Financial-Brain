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
(30 tests); Ruff and mypy pass for the changed modules. In addition to restart,
capital isolation, and cross-book order rejection, the live-paper service now
requires a registered plan, resolves the exact strategy allocation at the
decision timestamp, and checks the plan ID, account ID, canonical journal path,
and opening capital before preflight or session writes. Receipt reconciliation
submits the order through the registered-book family evidence and owner-approval
gate. Regressions reject mismatched capital and an unregistered journal without
additional account writes.

`PaperAccount.close()` now adds a durable, hashed close record only when the book
has no open inventory or working orders, available cash equals cash, and total
return reconciles. The close survives restart, is idempotent for the same close
identity, rejects altered retries, and makes the account journal read-only. This
records final account cash but does not itself authorize release or reallocation
of that capital from the registered-book budget.

Remaining work: authenticate the registration/change evidence through an
approved owner identity service; connect the closed-account receipt to registry
level released-capital accounting and successor allocation approval; wire any
legacy paper-account writers through the registered-book service. Direct
construction of `PaperAccount` remains possible outside that service, although
the durable account-close state itself is enforced by the account journal. No
live or realized strategy capital is implied.
