# PE-0074 marketable-limit quote and depth fill

The canonical paper execution adapter uses the first post-decision quote available after modeled latency. Marketable buys pay the tick-rounded ask and sells receive the tick-rounded bid, subject to the order limit, price band, and visible side-specific depth. Missing post-latency quotes or visible size produce an explicit missed outcome; no decision-bar close, LTP, or mid-price fill is substituted.

The task is `DONE` for the simulator contract: deterministic quote tests show that a one-tick adverse quote worsens each side's fill price and that missing depth cannot fill. This is quote simulation evidence, not an observed broker-fill claim.
