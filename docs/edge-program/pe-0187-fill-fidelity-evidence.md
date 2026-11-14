# PE-0187 — Fill fidelity and execution inputs

## Acceptance

Show simulated versus observed execution labels and source inputs alongside each fill so a modeled quote fill cannot be confused with an exchange fill.

## Implementation and verification

`MarketableQuoteSimulator` receipts now attach the selected quote observation to each simulated fill: instrument/exchange, declared provider source, bid/ask and displayed sizes, event/received/available clocks, quote hash, and raw archive references when present. `cockpit.execution_fill_table` shows those inputs and execution/fee/latency/data hashes beside the fill. Simulated fills are explicitly labeled “SIMULATED ONLY — NOT AN EXCHANGE FILL.” Observed-broker labels require provider fill ID, source/exchange, timestamp, source hash, read-only session identity, and reconciliation; otherwise the label is explicitly unverified. Empty receipts say NO FILL.

Verification: `tests/test_execution_fill_evidence_view.py`, `tests/test_forward_paper_execution_adapter.py`, and `tests/test_forward_edge_f10.py` — 30 passed; Ruff and targeted mypy passed.

## Limits

The quote simulator is modeled paper execution, not an exchange fill. This change does not create or fetch observed broker executions. An observed row is only as trustworthy as the upstream source and reconciliation evidence supplied; the view does not authenticate a broker itself.
