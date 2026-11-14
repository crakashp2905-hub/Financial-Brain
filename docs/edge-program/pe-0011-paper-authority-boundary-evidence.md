# PE-0011 paper authority boundary

## Enforced forward-paper boundary

For `ForwardPaperSessionService`, the `ForwardSessionRunner` journal is the durable session/outbox evidence stream. The `PaperAccount` journal is the authoritative source for OMS orders, cash, positions, fees and double-entry accounting. They must resolve to different paths: the service rejects an alias before preflight or any session write, and reconciliation repeats the check before posting account events. `PaperAccountPortfolio` also rejects duplicate account objects, mismatched book IDs, shared durable journals and mixed currencies when building a cross-book view.

The regression `test_session_evidence_and_paper_account_authority_journals_cannot_alias` confirms alias rejection leaves the account journal unchanged. The combined forward-paper bridge, portfolio, registered-book and source-inventory suites passed (31 tests); Ruff and mypy passed for the bridge, and the source inventory passed its freshness check.

## Remaining isolation gap

The older `paper/intraday_runner.py` writes its own DuckDB trade/run tables. It is not bound to the forward `PaperAccount` authority mapping, so the system does not yet prevent the same strategy from being operated in both engines under overlapping live-paper scopes. The generated inventory covers 626 Python sources, with 619 unassessed and 358 role-unassigned modules. No global single-authority claim is made.
