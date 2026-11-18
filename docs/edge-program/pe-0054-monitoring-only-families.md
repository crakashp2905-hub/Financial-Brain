# PE-0054 monitoring-only intraday and chart-pattern families

Registry policy now recognizes normalized family names beginning with `orb`, `opening_range`, `gap`, `chart_pattern`, `candlestick`, `candle_pattern`, or `directional_chart_pattern` as monitoring-only. A spec from one of these families cannot be constructed directly in a paper-capable status and cannot transition from `SHADOW_ONLY` to `PAPER_ELIGIBLE`. This applies regardless of caller-supplied approval metadata.

The initial monitoring catalogue declares separate candidates for opening-range breakout, gap continuation, gap reversal, directional chart patterns, and chart formations. `monitoring_signal` requires the exact current `SHADOW_ONLY` registry spec and emits a quantity-free observation with an explicit non-promotion reason.

This is a family-level guard and a five-entry starter catalogue. The 72 chart-pattern inventory entries and every legacy ORB/GAP adapter are not yet individually registered or forced through this API. No prospective monitoring cohort has been collected, so the task remains partial.
