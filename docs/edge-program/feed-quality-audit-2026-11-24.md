# Feed-quality audit — 2026-11-24

Checklist item: PA-02-08 — detect partial candles, missing minutes, wrong interval anchors, session-open gaps, stale ticks, nonmonotonic timestamps, crossed markets, and out-of-sequence events.

## Implemented

- Added forward_edge.feed_quality.audit_feed, a deterministic report over an arrival-ordered event stream for a caller-specified venue, instrument, session, interval, and as-of clock.
- Findings include PARTIAL_CANDLE, MISSING_MINUTE, WRONG_INTERVAL_ANCHOR, SESSION_OPEN_GAP, STALE_TICK, NONMONOTONIC_TIMESTAMP, CROSSED_MARKET, and OUT_OF_SEQUENCE; sequence gaps and data unavailable at the audit cutoff are reported too.
- Missing-bar coverage counts only completed bars. The current in-progress candle is reported as partial rather than treated as missing.
- The helper returns a not-ready report on any finding. It does not modify or hide raw events.

## Verification

- python -m pytest tests/test_forward_edge_feed_quality.py tests/test_forward_edge_market_events.py tests/test_forward_edge_quote_archive.py -q: 36 passed.
- Scoped Ruff and mypy passed.
- Synthetic tests exercise all eight checklist findings, a clean complete series, missing session-open bars, unavailable bars excluded from coverage, and invalid naive clocks.

## Remaining limits

Status remains PARTIAL. The helper is not yet called automatically by ForwardSessionRunner, Kite ingestion, or every feed adapter. Production session calendars and representative provider streams have not been used to establish actual coverage or safe signal gating. Callers must currently invoke the report and enforce its readiness themselves.
