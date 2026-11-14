# PE-0184 — Reconciled strategy comparison

## Acceptance

Compare gross result, costs, net, benchmark net, alpha, confidence interval, drawdown, turnover, fidelity, and sample. Sorting must not hide failed or insufficient candidates by default.

## Implementation and verification

`forward_edge.cockpit.strategy_comparison` renders all rows in a static, read-only comparison table with the required metrics. Failed and insufficient rows sort before qualifying rows. Missing sample/evidence is visibly treated as insufficient; no pagination or status filter can omit registered rows. When supplied, it validates `net = gross - costs`, `alpha = net - benchmark_net`, finite numeric inputs, and interval order.

Verification: `tests/test_forward_edge_f10.py` — 20 passed; Ruff and targeted mypy passed.

## Limits

This is a presentation and arithmetic-integrity layer. The caller must provide correct, comparable evaluation records and confidence intervals. It does not estimate performance or imply profitability; it exposes the status and fidelity supplied by the evidence pipeline.
