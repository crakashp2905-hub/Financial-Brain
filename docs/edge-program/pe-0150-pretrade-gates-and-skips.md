# PE-0150: Pre-trade gates and durable skip attribution

**Status: PARTIAL.** Added a frozen, content-addressed policy evaluator for
quote freshness/spread, displayed quantity and dated ADV capacity, costed net
alpha, projected exposure, and model confidence. Its risk-verdict adapter
provides the full named checks and source evidence identities. The forward
session runner now records every explicit `NO_TRADE`/`REJECTED` and missed or
rejected execution as an immutable `PAPER_SKIP`, including stable candidate or
intent identity, signal/input hashes, failed risk checks, preflight, execution
reason, and decision details. Replays must reproduce the same skip record.

Validation: `python -m pytest -q tests/test_pretrade_gates.py
tests/test_decision_sealing.py tests/test_forward_session_runner.py` (58 passed)
and Ruff passed.

The evaluator remains an injected risk adapter. The repository does not yet bind
all strategy sessions to one authoritative ADV source, frozen cost table,
portfolio exposure book, calibrated confidence record, or eligibility registry.
Those inputs and the callback must be connected before claiming every paper
session applies these gates. Skip records are durable even for sessions that
abstain before an order is formed.
