# PE-0143 — independent adversarial review before registration

`make_adversarial_review()` requires an author-distinct reviewer and a reasoned, evidence-linked finding for leakage, costs, multiple testing, chance fitting, and the prewritten falsifier. Any failed area records `REJECTED`; missing evidence records `NEEDS_REVISION`. Approval requires every area to pass, the review to precede registration, and a trusted receipt over the exact proposal, draft strategy, reviewer identity, findings, rationales, and evidence IDs. `AdversarialReviewJournal` checks the review digest, eligibility invariants, and receipt status against trust roots supplied at journal construction before it stores an approval. Review decisions are append-only and immutable. The review API cannot mutate a paper strategy.

**Status: PARTIAL.** Twenty-three focused adversarial-review, hypothesis, and evidence-label tests pass; Ruff passes. Receipt verification still depends on trust roots supplied by the caller; no approved internal identity provider or deployment-pinned reviewer roots are configured. The archive is not connected to the proposal/registration workflow, and synthetic signed test receipts do not constitute an actual independent review.

Evidence and source hashes are recorded in `pe-0143-adversarial-preregistration-review-evidence.json`.
