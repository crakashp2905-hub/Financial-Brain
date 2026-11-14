# PE-0096 — Multi-currency cash and FX translation

The new `MultiCurrencyBook` ledger primitive requires timestamped, source-hashed fixings, posts explicit per-currency clearing legs for conversion, and records FX revaluation P&L separately in the reporting currency. It never retrieves or invents rates.

The helper is currently an isolated ledger capability. Durable `PaperAccount` integration, replay auditing, and an authorized rate source remain outstanding, so PE-0096 stays partial.

Evidence and source hashes are in `pe-0096-multicurrency-fx-evidence.json`.
