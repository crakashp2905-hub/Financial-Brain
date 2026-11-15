# PE-0129 — critical falsification gates

The falsification report requires evidence for look-ahead, survivorship, delisting, corporate actions, stale quotes, missing fills and factor overlap. It also retains rejected-trade and missed-fill checks. A failed check yields `FAIL`; an absent check remains `INSUFFICIENT_EVIDENCE`; unknown keys and non-boolean outcomes are rejected. `FalsificationRegistry` binds the full result set and per-check artifact digests to the frozen prospective cohort hash in an append-only journal. `promotion_verdict` resolves status from that registry; a caller-supplied clean status without a registered report cannot promote.

**Status: PARTIAL.** The complete-suite verification covers the registry and gate; focused tests cover every critical check, immutable cohort binding, restart, missing artifact digests and fail-closed promotion behavior. Ruff and mypy pass. Artifact digests are still caller-supplied and do not independently prove that checks ran or that the referenced data is complete and truthful. Independent evidence custody and data-backed check adapters remain necessary.

Evidence and source hashes are recorded in `pe-0129-critical-falsification-gates-evidence.json`.
