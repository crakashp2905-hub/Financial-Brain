# PE-0059 frozen benchmark definitions

The forward-edge registry requires a content-addressed benchmark definition with a freeze date, treats duplicate registration of identical content as idempotent, and rejects rewriting an existing benchmark ID. Paper enrollment requires the strategy's benchmark to be registered and frozen before the charter start date.

The task remains partial: this registry is not yet transactionally coupled to every exploratory result store, so it cannot prove that no benchmark was inspected or changed after any historical/shadow outcome across all research surfaces. Frozen cohort manifests and result publication still need to consume this registry consistently.
