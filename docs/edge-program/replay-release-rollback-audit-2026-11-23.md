# Replay-before-release and rollback audit — 2026-11-23

Checklist item: **PA-01-14** — “Add deterministic replay-before-release and rollback rehearsal; restore the prior known-good release if any net-P&L or ledger consistency invariant fails.”

## Existing controls

- `.github/workflows/ci.yml` runs `tests/test_forward_edge_cli.py::test_fixture_pack_is_deterministic_and_costs_are_net_of_pnl` in the reproducibility matrix.
- `scripts/forward_edge_equivalence_report.py --check` checks a frozen suite of replay outcomes against legacy golden traces and integrated paper reports. The checked-in report records fixture-level cash reconciliation, ledger balance and costed realized net P&L eligibility.
- `src/financial_brain/forward_edge/champion_governance.py::automatic_rollback` can switch a paper champion to the frozen safe policy and `SAFE_HOLD`; `tests/test_forward_edge_champion_governance.py::test_automatic_rollback_enters_safe_hold_and_blocks_new_orders` exercises that primitive.

## Missing release acceptance

These pieces are not joined into a release transaction. CI does not reject publication specifically on a replay net-P&L or ledger invariant failure and then rehearse restoration of an identified prior known-good release. The champion rollback primitive is not an application-release pointer, and the current runner has no release activation/rollback workflow. No rollback rehearsal result is recorded.

**Status: PARTIAL.** Remaining acceptance work: define immutable current/prior release identities; run the deterministic replay before activation; inject both net-P&L and ledger-consistency failures; prove the prior release is restored and no pending paper intents are duplicated or lost; and gate release promotion on the rehearsal result.
