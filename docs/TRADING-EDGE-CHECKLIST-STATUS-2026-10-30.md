# Trading-edge checklist: implementation status (2026-10-30)

The attached technical checklist is a program specification, not proof of an edge. The 2026Q4 protected trial budget and final holdout must remain untouched. No code change here establishes profitable or deployable alpha.

## Implemented and verified

- `main` is protected on GitHub with strict Ubuntu/Windows CI checks and one approving review; stale approvals are dismissed. Administrator enforcement is off for emergency maintenance. This setting was verified through the GitHub API after commit `66baa36` passed both platforms.
- Champion cohort resolution no longer silently drops selected names without a forward price. It stores selected/observed/missing counts, survivor-only net, a -100%-for-missing sensitivity, universe coverage and the same universe sensitivity. Incomplete cohorts cannot satisfy promotion. This is a sensitivity, **not** a claim that every missing security lost 100%; corporate-action lineage and suspension/delisting reconciliation remain separate work.
- Local pick hashes remain integrity evidence, but `verified_days()` now fails closed without an independently verified external receipt. `attach_anchor()` stores a candidate receipt before the earliest next entry; an explicitly configured verifier must validate its external signature and timestamp on every read. No trusted provider is configured, so there are currently **zero promotion-grade verified days** from this path.
- OMS shortfall now rejects mixed-side/mixed-benchmark children and missing filled prices, reports unfilled share, and does not discard zero-prediction comparisons. New adversarial tests cover those branches.
- Historical stress replay retains finite 50%+ crashes and reports constituent coverage rather than quietly suppressing extreme returns.
- The paper-engine additions in this worktree provide versioned isolated intraday cohorts, cost gates, long-option quote replay, a local forward-intent ledger and defined-risk debit-vertical envelopes. They remain research/paper-only; external attestation, actual broker margin, expiry settlement and live fills are not claimed.

## Still open — requires further engineering or external evidence

- Supported dependency matrix/lockfile; performance baselines, release provenance and backup/restore drill.
- Independent signed time-anchor provider and verification implementation; source-specific market calendars/freshness; complete decision snapshot hashes.
- Suspended/delisted lineage, corporate-action and missing-outcome adjudication; full-market PIT universe; purged evaluation, dependence-aware intervals and family-wise trial accounting.
- OMS evidence/MCP/risk exposure adversarial coverage and per-module CI minimums; market-hours failure drills.
- Real prospective nonoverlapping champion/challenger cohorts, paired live-paper/replay fills and observed option quote/depth coverage. These cannot be backfilled as forward evidence.
- Options expiry/physical-settlement, broker margin, synchronized multi-leg execution, and portfolio-level actual cash/holdings accounting.
- Data licensing, trader task studies, security/recovery rehearsals, commercial and any live-capital validation.

No current result should be labelled `EDGE_CONFIRMED` or used to arm live orders. Existing trials and the protected holdout were not rerun or modified for this implementation.
