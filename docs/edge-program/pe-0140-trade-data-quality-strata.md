# PE-0140 — trade outcomes by market-data quality

`assess_trade_data_quality()` applies thresholds frozen before the observation window and associates each trade with feed gaps overlapping its decision-to-entry interval, stale quote age, abnormal spread, and high-volatility flags. It retains issue evidence IDs, summarizes trade counts/net INR/losing counts by `SYSTEM_ISSUE`, `CLEAN_OBSERVED`, and `EVIDENCE_INCOMPLETE`, and never treats absent coverage as clean. Only a fully covered entry with identified quote/volatility evidence can be labeled clean. Data-quality strata are reported separately from the strategy outcome.

**Status: PARTIAL.** Forty focused trade-quality, feed-health, and F03 tests pass; Ruff passes. This API accepts supplied trades, feed-gap intervals, and evidence IDs; it is not yet subscribed to the durable quote/health journal or surfaced in the daily paper report. Its strata are descriptive and cannot establish that a system issue caused a loss or that clean-data outcomes demonstrate strategy edge.

Evidence and source hashes are recorded in `pe-0140-trade-data-quality-strata-evidence.json`.
