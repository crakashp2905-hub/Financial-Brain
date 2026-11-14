# PE-0169 — Reconciled economics by asset class

## Acceptance

Each scheduled paper session must carry a canonical account snapshot and hash-verified cumulative attribution for CASH, EQUITY, OPTIONS, FUTURES and FX. Per-class gross P&L, fees, taxes, other costs, and net contribution remain separate. Their totals must reconcile to the canonical account archive on every observed date. Any missing class, missing session, account mismatch or incomplete account archive blocks aggregate P&L reporting; a positive equity contribution cannot erase separately displayed options/futures losses.

## Implementation and verification

`financial_brain.forward_edge.asset_class_edge.build_asset_class_edge_report` consumes the frozen session denominator, canonical account archives and per-class cumulative snapshots. The `python -m financial_brain.forward_edge.cli asset-class-report --input <json>` command exposes the same report path for stored paper evidence. It differences cumulative values into report-window contributions, checks attribution hashes and cost arithmetic, and compares every class sum to the account's gross, fees, taxes and economic return. It reports the five asset classes individually and only emits aggregate net contribution when coverage is complete and reconciled. Missing or conflicting evidence leaves the aggregate blank. A complete accounting report is explicitly labeled as attribution only; it cannot be used by itself to claim positive alpha or validated profitability.

The focused suite has six tests for reconciled gains/losses across equity/options/futures, missing classes, account/class mismatch, duplicate or tampered class evidence, omitted scheduled dates, and CLI incomplete-evidence behavior. Ruff, targeted mypy and source-inventory checks must pass before the package is recorded.

## Limits

Tests use synthetic paper-account archives and synthetic asset-class attributions. This function is a report builder; wiring each production book to submit properly classified account rows and prospective evidence remains required before any real cohort can use it. It produces accounting contribution, not benchmark-relative edge, risk-adjusted utility or a profitability claim.
