# Chart patterns: offline development policy (CP-0009)

Fixtures and synthetic data are allowed for development and tests. Fresh market evidence is never synthesised.

Every gate that depends on something outside this checkout is recorded in one of three categories and is not marked finished:

- **OWNER**: a decision or account the project owner must make or provide (for example, approving a release tag or a live-trading decision).
- **DATA_TIME**: evidence that can only come from time passing, such as a real market-time forward cohort or independent timestamp custody.
- **LICENSE**: data or redistribution rights the project does not hold, such as exchange order-book depth or index membership.

Rules that follow:

1. Development uses deterministic fixtures and offline adapters. No test calls a network service, data vendor or broker.
2. Synthetic results are labelled SYNTHETIC and never presented as market performance.
3. A code task that depends on a gate in one of these categories is completed only up to its offline boundary. The gate itself stays open.
4. The residual list lives in `docs/audits/PATTERN-FINAL-GATES.md`.
