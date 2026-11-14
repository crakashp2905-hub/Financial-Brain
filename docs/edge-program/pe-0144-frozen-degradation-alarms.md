# PE-0144: Frozen-threshold degradation alarms

**Status: PARTIAL.** The monitor evaluates evidence-linked rolling net alpha,
slippage, fill coverage, and drift against a content-addressed policy frozen
before a paper cohort starts. Missing or insufficient evidence pauses the
candidate. Breaches pause after the configured consecutive count; repeated
net-alpha breaches at the separate retirement threshold retire it. Pause and
retirement are latched. Recovery does not silently resume the candidate.

Alarm events identify the strategy version, policy hash, observation, reasons,
and consecutive breach counts. Thresholds are immutable, and the monitor has no
strategy-parameter mutation interface. Callers must persist returned events in
the durable evidence journal and connect the decision to the paper runner's
candidate lifecycle.

Validation: `python -m pytest -q tests/test_degradation_monitor.py` (7 passed)
and `ruff check src/financial_brain/forward_edge/degradation_monitor.py
tests/test_degradation_monitor.py` (passed).

Remaining work: integrate the monitor with prospective paper observations and
durable session journaling; establish production policy and identity roots; run
the monitor on real forward-attested cohorts; verify alarm delivery and pause
enforcement in an end-to-end session. This package makes no claim of observed
alpha, realized performance, or production activation.
