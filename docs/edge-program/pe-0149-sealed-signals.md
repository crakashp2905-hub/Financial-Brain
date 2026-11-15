# PE-0149: Seal signals and abstentions before outcomes

**Status: DONE for the checklist acceptance criterion.** Every new
`ForwardSessionRunner` decision—actionable signal or explicit abstention—is
persisted with a decision/seal timestamp, strategy and run identity, hashes of
all source events available at that decision, preflight and feed-health identity,
a canonical input-manifest hash, and a signal hash. The runner creates this
record before risk adjudication, paper execution, or session outcome. Future
events are excluded from the decision input list and rejected by the sealing
function. Abstentions receive an explicit reason code.

Validation: `python -m pytest -q tests/test_decision_sealing.py
tests/test_forward_session_runner.py` (48 passed) and Ruff passed.

The seal timestamp is the session decision clock and the event is durably
appended before the runner advances. It is not an external trusted timestamp;
independent timestamp attestation remains a separate evidence requirement.
