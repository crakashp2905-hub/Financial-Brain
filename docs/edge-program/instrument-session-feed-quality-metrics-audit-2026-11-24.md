# Instrument/session feed-quality metrics — 2026-11-24

Checklist item: `PA-02-12` — report feed quality by instrument and trading day/session.

## Implemented

`forward_edge.feed_quality.audit_feed` now returns a `FeedQualityMetrics` summary keyed by venue, instrument, and the caller-supplied session-open date alongside existing defect findings. `ForwardSessionRunner.close_session` runs this audit from its durable session event journal and calendar plan, then embeds the metrics, bounded finding samples, and a digest of the source event set in the hashed close report. Sessions without sourced calendar bounds record quality status `UNAVAILABLE` with a reason.

- completed valid bars / expected completed intervals and coverage fraction;
- late ticks / available ticks and late-tick rate, using a configurable threshold (default one second);
- quote age p50, p95, and maximum at the `as_of` cutoff;
- two-sided quote depth validity fraction, checking positive quantities, sorted levels, and top-level consistency with best bid/ask;
- inconsistent OHLCV count, with invalid bars excluded from coverage;
- mean absolute relative strike-selection error when an option payload supplies both `selected_strike` and `target_strike`.

Metrics only count facts available by the audit cutoff. A missing denominator or missing selection pair yields `None`, not a fabricated zero. Strike error is explicitly a relative distance from the provided target strike; it does not assess whether the target itself was the economically correct strike.

## Verification

- `python -m pytest tests/test_forward_edge_feed_quality.py -q`: 8 passed.
- Scoped Ruff, mypy, and `git diff --check` passed.
- The new fixture test covers date-keyed metrics, valid-bar coverage, late-tick rate, quote age, valid depth, mutated inconsistent OHLC, and relative strike-selection error.
- `tests/test_forward_edge_session_runner.py::test_close_report_binds_feed_quality_metrics_to_persisted_market_events`: 1 passed; verifies the close report binds coverage and missing-minute findings to the persisted event set and is idempotent on retry.

## Remaining limits

Status remains `PARTIAL`. The close report persists the summary but does not yet enforce a readiness gate or cover raw packets rejected before `MarketEvent` creation. No representative authorized provider/depth archive validates the rates. Strike-selection error is unavailable unless the caller supplies a defensible target and selected strike in the event payload.
