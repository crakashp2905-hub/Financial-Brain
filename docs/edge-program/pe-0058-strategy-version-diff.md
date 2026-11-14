# PE-0058 strategy-version diff

Added an immutable `StrategyVersionSnapshot` and deterministic diff API. The snapshot requires content hashes for strategy code, parameters, filters, universe, cost model and benchmark, plus explicit asset scope and horizon. A diff accepts only adjacent versions of the same strategy, exposes each changed field and category, binds both snapshots and the full diff by SHA-256, and distinguishes a version-number-only change from a scientific identity change.

The API is currently standalone. The legacy strategy inventory and all execution/registry surfaces do not yet export complete filter, universe and cost-model identities into this snapshot, so existing candidates cannot be diffed automatically without additional manifests. The task remains partial.
