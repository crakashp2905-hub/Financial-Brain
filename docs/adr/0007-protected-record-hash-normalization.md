# ADR-0007: Canonical hashes for protected records

Status: **Accepted** — this changes only the representation used for integrity
hashes. Protected records remain byte-for-byte unchanged.

## Context

Git stores text blobs with LF line endings. A checkout may materialize CRLF, and
the protected trial ledger also contains mixed line endings. Hashing checkout
bytes directly made the same pinned ledger pass on one platform and fail on
another, even though its committed content was unchanged.

## Decision

Compute protected-record SHA-256 values after replacing CRLF pairs with LF.
Record that contract in both protected-pin manifests and update their digests
to the canonical content hashes. Do not rewrite `docs/trials/ledger.jsonl`,
change any verdict, add a trial, or alter a trial budget.

## Verification

`tests/test_forward_edge_f00.py::test_protected_hashes_are_stable_across_lf_and_crlf_checkouts`
proves newline-independent hashing. The full protected-trial tests and
`scripts/validate_trial_program.py` verify that all 13 Q4 trials remain failed,
the budget remains exhausted, and the records match the canonical pins.

## Consequences

Linux, Windows, and mixed-newline checkouts verify the same protected content.
The pin manifests describe canonical Git text content rather than a particular
working-copy byte layout.
