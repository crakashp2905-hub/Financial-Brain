# PE-0060 shared signal-to-target allocator

Added a single research-only adapter that turns named `BUY`/`HOLD`/`SELL`/`NO_SIGNAL` observations and itemized `NetAlpha` forecasts into target weights through the common risk-aware portfolio optimizer. The adapter enforces a per-horizon forecast net-alpha hurdle for additions, passes economic net alpha separately from covariance risk penalties, requires all signals to share a forecast horizon, and refuses to orphan a currently held instrument without an explicit observation. Cash remains an explicit target weight.

This is not yet wired into every strategy adapter or the forward session paper-account bridge. It also consumes caller-supplied forecasts; it does not establish their calibration or empirical validity. The task remains partial pending end-to-end book integration, versioned covariance/universe inputs, and prospective validation.
