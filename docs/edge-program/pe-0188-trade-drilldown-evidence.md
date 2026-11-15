# PE-0188 — Immutable per-trade drilldown

## Acceptance

Provide the chain source quote → signal → risk → intent → fill → ledger → exit → attribution, with every displayed field linked to an immutable journal ID.

## Implementation and verification

`forward_edge.cockpit.trade_drilldown` requires all eight stages, unique stage identities, stable journal IDs, and record SHA-256 values. It verifies durable journal envelopes against their previous hash and payload, verifies content-addressed records and paper archive snapshots, and confirms displayed fields are exact subsets of their hashed record. Every field links to an in-page rendering of the corresponding raw record and digest; missing, duplicate, or altered stages fail closed.

Verification: `tests/test_trade_drilldown_view.py` — 6 passed; combined drilldown/cockpit run — 26 passed. Ruff and targeted mypy passed.

## Limits

The caller must assemble the eight records from their authoritative journals and supply correct stage relationships. This view verifies record integrity and field binding, not the semantic truth of an upstream signal, source quote, fill, or attribution. Open positions need an explicit journal-linked exit state rather than an invented completed exit.
