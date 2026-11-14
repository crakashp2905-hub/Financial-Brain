# PE-0183 — Session readiness overview

## Acceptance

Show feed health, market-calendar state, active strategy version, read-only broker session, and risk latches. Stale or incomplete sources must be prominent and prevent trade-readiness claims.

## Implementation and verification

`forward_edge.cockpit.session_overview` renders a read-only static session page from caller-supplied evidence. It checks freshness against explicit per-source limits, requires complete feed provenance and calendar/broker evidence hashes, checks a frozen active artifact identity, and shows every risk latch. Missing, invalid, future, or stale timestamps produce visible blocker codes and a blocked readiness verdict.

Verification: `tests/test_forward_edge_f10.py` — 18 passed; the combined cockpit/preflight/source inventory run had 40 passed. Ruff and targeted mypy passed.

## Limits

This is a render-only function: it does not fetch Kite, query a broker, validate the exchange calendar itself, or clear risk latches. The caller must supply trustworthy and current evidence. “Ready” means these provided paper-session controls are present; it is not profitability, order authorization, or live-trading readiness.
