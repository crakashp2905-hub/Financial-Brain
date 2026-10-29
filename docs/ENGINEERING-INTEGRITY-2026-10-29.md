# Engineering integrity checkpoint — 2026-10-29

This checkpoint is about implementation and evidence, not deployable alpha.

## Confirmed CI failure and local fix

The [2026-10-29 GitHub Actions run](https://github.com/crakashp2905-hub/Financial-Brain/actions/runs/40813818332)
failed in both Ubuntu and Windows test jobs. Install, Ruff, mypy and migrations
passed. The failing test setup reached `features/technical.py` and DuckDB rejected
its deprecated `(x, i) -> ...` lambda syntax. The MACD SQL now uses
`lambda x, i: ...`; the affected local technical/feature tests passed. The workflow
now emits a critical-module coverage report and XML artifact for each OS. This
does **not** make CI green until a new remote run succeeds.

Local targeted coverage from 62 passing tests and one skipped test was 49% over the
selected critical-module group; the full suite should raise this. The report exposed
especially weak coverage of strategy health, risk allocation and OMS shortfall.
No arbitrary threshold was added before measuring the complete CI baseline.

## Champion date and attestation contract

- `champion.picks` now requires its actual session date; its scanner challenger no
  longer silently receives the 2026-10-05 lock date for every later session.
- New forward records require the price archive to end on the pick day, refuse
  backfills/overwrites, and require the completed cash session for same-day picks.
- Each new attestation stores a SHA-256 of the actual pick batch. `verified_days`
  and displayed standings exclude rows whose digest no longer matches. Historical
  picks lacking the digest are never retroactively certified.
- A local clock and a local hash are error-detection and workflow controls. They
  are **not** independent proof that a decision existed before its outcome. A
  signed external timestamp or immutable remote audit log would be a stronger gate.

## Economic quantities

`NetAlpha.net` is now expected excess return minus fees, spread and impact.
`NetAlpha.utility` subtracts the variance penalty separately. The opportunity
chain also reports `risk_adjusted_utility` and `portfolio_utility` rather than
calling either an economic return. Scanner ranking uses *modelled* economic net
alpha. Existing saved snapshots were built under the old convention: regenerate
the terminal snapshot before comparing new scores to old ones, and do not blend
historical and new rankings without a versioned backfill.

## Open acceptance gates

The local source changes cannot create elapsed forward cohorts, same-day
live/replay pairs, licensed historical depth, validated trader usefulness,
commercial authorization or live-capital approval. Those items remain open. CI
branch protection also requires repository settings access and a successful new
run; a local workflow edit alone cannot impose it.
