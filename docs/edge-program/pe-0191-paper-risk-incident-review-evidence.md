# PE-0191 — Paper risk kill switch and incident review

## Acceptance

Expose the paper-only risk kill switch and incident review with a human-controlled reset. AI and untrusted UI actions must not silently override a halt.

## Implementation and verification

`PaperAccount` persists latch trip/reset events in its hash-chained journal and blocks new paper intents while active. Reset now rejects agent/AI/automation/script/model actors using the shared human-actor validator and still requires the incident ID, repair ID, aware sign-off time, and sign-off SHA-256. `cockpit.risk_incident_review` reopens the journal through `DurableJournal` verification, displays every incident and current latch state, and renders no action elements or reset controls.

Verification: `tests/test_risk_incident_review.py`, `tests/test_paper_account_risk_latch.py`, and `tests/test_forward_edge_f09.py` — 22 passed; Ruff and targeted mypy passed. Tests cover active halt, rejected AI/automation reset attempts, visible human reset audit, and tampered journal refusal.

## Limits

The kill switch applies to this paper account. Reset evidence is a recorded human sign-off digest, not a cryptographic signature verified against a separate identity provider. The cockpit is read-only and cannot reset the latch. No broker/live-order path is enabled.
