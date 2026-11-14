# PE-0144: Frozen-threshold degradation alarms

**Status: PARTIAL.** The monitor evaluates evidence-linked rolling net alpha,
slippage, fill coverage, and drift against a content-addressed policy frozen
before a paper cohort starts. Missing or insufficient evidence pauses the
candidate. Breaches pause after the configured consecutive count; repeated
net-alpha breaches at the separate retirement threshold retire it. Pause and
retirement are latched. Recovery does not silently resume the candidate.

Alarm events identify the strategy version, policy hash, observation, reasons,
and consecutive breach counts. Thresholds are immutable, and the monitor has no
strategy-parameter mutation interface. An optional `DurableJournal` sink writes
each alarm into the hash-chained paper journal before returning it; reopening the
monitor recovers the breach counts, duplicate guard, and latched pause/retirement
state. A failed journal append leaves in-memory monitor state unchanged. The
forward session runner binds a rolling observation to a completed paper session
and withholds new or increasing exposure in later sessions when its registered
policy is latched at `PAUSE` or `RETIRE`. Explicit `REDUCE` and `CLOSE` decisions
remain eligible so a paused strategy can exit existing exposure.

Validation: `python -m pytest -q tests/test_degradation_monitor.py
tests/test_forward_session_runner.py` (64 passed), Ruff, and mypy passed.

Remaining work: schedule a trusted rolling-metric adapter after every forward
paper session; establish production policy and identity roots; run the monitor
on real forward-attested cohorts; verify alarm delivery and pause enforcement in
an owner-authorized market session. This package makes no claim of observed
alpha, realized performance, or production activation.
