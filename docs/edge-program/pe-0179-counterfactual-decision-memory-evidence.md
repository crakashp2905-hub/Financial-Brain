# PE-0179 — Counterfactual memory for rejected opportunities

## Acceptance

Retain rejected opportunities with an explicit hypothetical label and without inventing actual fills.

## Implementation and verification

The quote-depth shadow simulator records each rejected decision with `decision_nature=HYPOTHETICAL_REJECTED_OPPORTUNITY`, `execution_observed=false`, and `counterfactual_only=true`. The append-only `ShadowBookJournal` persists the full report under an immutable book identity and refuses records without those per-decision labels. Quote-backed hypothetical fills remain separate from actual execution, and no-fill/partial/unclosed results remain explicit.

Verification: `tests/test_shadow_counterfactual.py` — 9 passed, including persistence/hash identity, rejection memory, and false-fill label guards. Ruff passed.

## Limits

Counterfactual returns depend on a frozen hypothetical fill policy and observed quote inputs; they are not actual fills, observed cash P&L, or promotion evidence.
