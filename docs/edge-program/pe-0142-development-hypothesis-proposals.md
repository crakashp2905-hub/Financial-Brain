# PE-0142 — training-only candidate hypotheses

`propose_candidate_hypothesis()` forms a structured proposal from paired, matched-control DEVELOPMENT observations only. The plan freezes the development/validation windows, minimum daily sample, expected direction, minimum effect, mechanism, and falsifier before reading results. Each session contributes one matched aggregate and evidence ID; the function records the full row set, hashes the exact normalized observations, and reports whether the development estimate triggers the prewritten falsifier.

Proposals stay `PROPOSED_PENDING_INDEPENDENT_REVIEW`. The archive has no API to mutate paper rules or trial budgets; every record states strategy mutation `FORBIDDEN` and budget effect `UNCHANGED`. The numeric difference is an observational association, not a causal finding. Validation evidence must come from a separately registered trial and independent review.

**Status: PARTIAL.** Twenty-six focused hypothesis, loss-classification, and F07 tests pass; Ruff passes. The observation digest binds proposal content to the supplied rows but is not an authoritative registry receipt or source signature. Inputs remain caller-supplied and the proposal path is not connected to the trusted development-data registry, independent review workflow, or paper strategy registry. No real hypothesis is established by the synthetic tests.

Evidence and source hashes are recorded in `pe-0142-development-hypothesis-proposals-evidence.json`.
