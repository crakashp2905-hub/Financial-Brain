# PE-0148: Read-only Kite access in paper workflows

**Status: PARTIAL.** Tightened Kite market-data route validation from broad
prefix matching to exact read routes and constrained instrument/history path
components. Embedded queries, traversal, unknown suffixes, and non-API URLs are
rejected before credentials or network access. Paper-mode market-data integration
tests observe only GET requests for quotes, history, and instruments; attempted
order/trading routes never reach the HTTP transport. `LIVE_PAPER` remains unable
to send physical orders, and its normal execution adapter is simulated.

Validation: `python -m pytest -q tests/test_kite_paper_readonly.py
tests/test_phase3_kite.py` (27 passed) and Ruff passed.

The repository's public API has no broker-order adapter. This test proves the
Kite market-data transport is read-only and its route guard blocks the attempted
write paths. It cannot constrain arbitrary network code a caller might inject
as an execution callback, and the actual owner account/IP controls are not
verified here. Keep real broker order endpoints outside paper orchestration.
