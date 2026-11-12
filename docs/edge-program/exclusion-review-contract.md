# Session exclusion review

Session exclusions are limited to source-payload, timestamp, or schema corruption. Each request binds the exchange date, session ID, reason, rationale, reporter, and evidence SHA-256. A second, distinct human reviewer must approve the exact request hash with a nonexpired `DATA_EXCLUSION_REVIEW` approval. The append-only journal revalidates this scope and the approval timestamps on recovery; reviewed entries cannot be changed or replaced.

An excluded date remains in the frozen schedule denominator. Reports show scheduled, observed, approved-excluded, and unreviewed-missing dates; they include a fixed-denominator zero-fill diagnostic and an adverse sensitivity assigning -100% to each excluded or unresolved date. Any exclusion or unexplained missing date changes report status to `REVIEW_REQUIRED` and disables promotion through this report.

Actor IDs and the `human` actor type are values supplied to the library. The current repository does not authenticate people or prove that the reviewer is organizationally independent. Until identity integration exists, the recorded review is a distinct-actor control, not externally verified independent review.
