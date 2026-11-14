# PE-0129 — critical falsification gates

The falsification report now requires evidence for look-ahead, survivorship, delisting, corporate actions, stale quotes, missing fills and factor overlap. It also retains rejected-trade and missed-fill checks. A failed check yields `FAIL`; an absent check remains `INSUFFICIENT_EVIDENCE`; unknown keys and non-boolean outcomes are rejected. The promotion evaluator already blocks any status other than `NO_FALSIFICATION_FOUND`.

**Status: PARTIAL.** Fifty-two related tests and Ruff pass, including a test for each critical failure. The checks are still supplied as caller assertions, and the falsification report is passed separately to the promotion evaluator as a status string. A data-backed, registry-bound harness must still prove that every check actually ran and cannot be bypassed by a caller-provided clean status.

Evidence and source hashes are recorded in `pe-0129-critical-falsification-gates-evidence.json`.
