# PE-0056 structured mechanism hypotheses

The `forward_edge` strategy registry now requires every `StrategySpec` to include a frozen structured mechanism hypothesis with five parts: why the effect may exist, why it may persist, who may bear the cost, the market regimes in which it is expected, and an observable falsifier. Required text and regimes cannot be blank; the hypothesis is normalized, immutable, and has a canonical SHA-256 identity exposed by its strategy spec.

The current monitoring-only chart/gap/ORB catalogue, exploratory slow-factor specs, and futures-carry candidates each carry explicit family-specific hypotheses. Their wording is a research conjecture, not evidence of an edge; the candidates remain monitoring or shadow-only with the existing promotion controls.

This item remains partial because the broader 253-entry strategy inventory and legacy algorithm surfaces are not all registered with this schema, and no prospective data has tested these hypotheses. Existing evidence cannot be retrofitted into a confirmation claim.
