# Checklist completion evidence policy

The unified v3 ledger may use `COMPLETE` only when the row has all of the
following evidence:

1. A repository documentation file that describes or records the delivered
   behavior.
2. Test evidence recording an executed acceptance test, plus a test file that
   exists in the current checkout.
3. At least one acceptance artifact with a repository-relative path and a
   SHA-256 digest matching its canonical bytes (CRLF normalized to LF) in the
   current checkout.

`scripts/checklist_completion_gate.py` audits existing completion rows and
produces `completion-evidence-gaps.v1.json`. The report intentionally exposes
legacy rows that do not yet meet this policy. On pull requests, CI compares the
ledger to the base commit and rejects a changed or newly completed row unless
its documentation, test, and hashed acceptance artifact all validate.

Current limitations are visible in the gap report: historical completion
claims still need item-by-item evidence reconciliation. The transition gate
prevents adding unsupported completion claims while that reconciliation is in
progress; it does not convert legacy gaps into proof or claim all rows are
verified.
