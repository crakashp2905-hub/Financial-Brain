# PE-0185 — Portfolio NAV dashboard

## Acceptance

Show cumulative portfolio NAV including cash and open positions, and reproduce the canonical ledger row for every session.

## Implementation and verification

`forward_edge.cockpit.portfolio_nav` consumes immutable end-of-session paper archives and canonical `NAV_SNAPSHOT` rows from the account journal. It validates archive and inventory hashes, proves `equity = cash + marked open-position value`, matches each archived snapshot exactly to the canonical journal row, requires complete one-to-one session coverage, and renders the cash, open-position value, NAV, external flow, and archive digest for every session.

Verification: `tests/test_portfolio_nav_dashboard.py`, `tests/test_forward_edge_f10.py`, and `tests/test_eod_paper_archive.py` — 27 passed; Ruff and targeted mypy passed. The integration case includes a cash day and a marked open losing position.

## Limits

The dashboard requires valid EOD archives and account NAV journal rows from the same paper account. Missing marks, missing session archives, or mismatched canonical snapshots fail closed. It is not a broker statement or live account valuation.
