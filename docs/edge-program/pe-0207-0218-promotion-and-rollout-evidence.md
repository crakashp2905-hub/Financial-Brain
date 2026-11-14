# PE-0207–PE-0218 — rollout and promotion evidence

**Reviewed against:** `Financial_Brain_Real_Paper_Profitability_In_House_Master_Checklist_2026-11-12.md`.

## Implemented controls

- `promotion_policy.py` freezes a hash of promotion thresholds before the first observation and requires a human approval scoped to that exact hash. The minimum independent-session count now comes from the frozen `SampleSizePlan`; callers cannot set a standalone count.
- Promotion requires a positive risk-matched benchmark alpha lower bound, positive net-alpha lower bound, registered 3×3 cost/fill stress grid, positive adverse-selection case, multiplicity adjustment, and matching benchmark/cost/sample-design hashes.
- Incident review, ledger reconciliation, rights, and custody each require affirmative state plus a SHA-256 evidence reference. The code checks reference shape and presence; it does not authenticate external artifacts or certify their contents.
- `PromotionDecisionLedger` writes immutable, hash-chained decisions including failures. Its record explicitly sets `physical_orders=false` and `live_cash_permission=false`; edits and changed evidence for a reused decision ID are rejected.
- The rehearsal adapter is read-only and rejects order submission/cancellation. The separate mock adapter is labeled mock and only produces `DRY_RUN_ACCEPTED` responses.
- `RiskProposal` binds capital-at-risk, per-order limit, daily-loss limit, margin limit, and emergency-exit policy hash. Approval must match the proposal's computed hash.
- The disarm latch recognizes stale feed, custody failure, abnormal costs, ledger mismatch, reconciliation mismatch, risk breach, unexpected execution, stale broker state, and lost heartbeat. Re-arming requires a scoped human `DISARM_RESET` approval. `scaling_evidence_gate` blocks scaling on statement mismatches, fill-model error, risk, reviewer, or owner-approval failures.

## Validation

- `python -m ruff check ...` on the changed promotion, approval, rehearsal source and tests: **passed**.
- `python -m mypy ...` on promotion, approval and rehearsal source: **passed**.
- Focused promotion, rehearsal, confirmation, challenger-registry and fixed-look suite: **82 passed**. After updating the existing Kite-provider guard to permit GET-only status reads while rejecting mutation routes, the affected provider/rehearsal/promotion regression set passed **66 tests**. The prior full run had 4,077 passed, 1 skipped and only those two outdated guard assertions failing; the full suite has not yet been rerun after the guard update.
- Promotion-ledger tests cover failed decisions, immutable idempotence, changed-evidence rejection, and tamper detection.
- These are code and synthetic-test results. They are not evidence that any strategy is profitable or that broker, custody, rights, legal, or live-session checks have occurred.

## Per-task status

| ID | Status | Remaining acceptance evidence |
| --- | --- | --- |
| PE-0207 | PARTIAL | No owner-approved promotion policy has been signed for a real candidate/cohort. |
| PE-0208 | PARTIAL | Sample floor derives from a frozen power/dependence design; real eligible nonoverlapping session receipts are absent, and session-date lists still arrive through a caller-provided cohort record; receipt contents are not independently authenticated by the evaluator. |
| PE-0209 | PARTIAL | Gate requires positive benchmark-relative lower bound; no actual prospective risk-matched returns, cost inputs, or benchmark report is available. |
| PE-0210 | PARTIAL | Full stress grid is a hard gate; grid has not been populated from observed broker fills and calibrated adverse-selection data. |
| PE-0211 | PARTIAL | Evidence references are required, but authoritative incident, rights, custody, and reconciliation artifacts are not connected/authenticated by this gate. |
| PE-0212 | DONE | GET-only Kite quote/order/position/holding status routes are allowlisted; the rehearsal adapter inherits blocked order methods and the mock is separately labeled. |
| PE-0213 | EXTERNAL_BLOCKED | Requires owner-authorized, real market-hours broker rehearsals and recorded clean-session receipts. None are synthesized. |
| PE-0214 | PARTIAL | Compliance gate fails closed on missing permission facts; verified venue/broker and applicable Indian regulatory evidence is not present. |
| PE-0215 | PARTIAL | Proposal captures all required risk fields and exact-scope human approval; no owner-confirmed proposal exists. |
| PE-0216 | PARTIAL | Scaling gate requires observed reconciliation and fill error evidence, but no contract-note/fill sample is available. |
| PE-0217 | PARTIAL | Trigger recognition and human-only re-arm are tested; production monitors are not wired to persistently actuate the latch. |
| PE-0218 | DONE | Immutable failure decisions are preserved in a hash-chained ledger; no live cash permission or physical-order unlock is emitted. |

**No strategy promotion, capital authorization, or live-order enablement follows from this package.**
