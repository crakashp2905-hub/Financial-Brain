# PE-0061 paper-entry net-alpha hurdle

`ForwardSessionRunner` now blocks new paper and shadow exposure unless the decision carries a finite expected net alpha, a positive forecast horizon, an explicit cost-inclusion marker, and a cost-itemized forecast. The runner independently recomputes net alpha as expected return minus benchmark return, fees, spread, and impact, verifies the decision value against that result, then requires it to be strictly greater than the frozen `minimum_forecast_net_alpha` in the session configuration.

The threshold is expressed as a fractional return over the forecast holding horizon. It defaults to zero, which still rejects zero or negative net alpha. The configured threshold is part of the session's content-addressed configuration. Risk rejections retain their original reason while the hurdle verdict is persisted alongside the risk verdict. A SELL decision must explicitly mark `position_effect` as `REDUCE`, `CLOSE`, or `INCREASE`; short entries use the same net-alpha hurdle. Reductions and closes do not need an entry forecast.

The forecast decomposition is carried in the immutable decision record, so session replay uses the same values. A low-edge candidate is surfaced as `NO_TRADE`, with the original candidate and its rejected forecast preserved in a durable disposition record for counterfactual analysis. Missing, inconsistent, non-finite, negative-cost, or below-hurdle forecasts fail closed before a paper intent or simulator call can be created.

## Evidence boundary

Synthetic tests establish gate behavior only. They do not validate forecast calibration, source-data quality, live execution, or profitability. Existing strategy adapters are not yet all wired to emit this forecast contract; those entries abstain until they do. Threshold selection and forecast validation remain prospective research tasks.
