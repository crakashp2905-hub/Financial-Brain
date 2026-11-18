# Strategy variant inventory (PE-0045)

`strategy_variant_inventory.py` builds a deterministic source-linked candidate inventory across historical failed trials, paper-zoo strategy IDs and rules, the bounded technical search catalogue, chart-pattern source definitions, options/futures modules, ranking-portfolio modules, and Python strategy modules. Each row carries the unique `(family, name, version, asset, clock)` key, code/source SHA-256, a variant SHA-256, status/category, and catalogue granularity. `--check` fails when the committed inventory no longer matches its sources.

The catalogue separates exact variants and recorded trial rows from `MODULE_PROXY` entries. Missing asset or decision-clock details stay `UNSPECIFIED`; no execution eligibility follows from appearing in the inventory. Current counts and unqualified fields are preserved in the generated JSON.

This is a first source-linked inventory, not a complete registry of every option/futures parameter combination or portfolio configuration. The remaining module proxies and unspecified asset/clock metadata must be resolved before calling the checklist requirement complete.
